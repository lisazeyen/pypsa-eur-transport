# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2020-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
"""
Prepares brownfield data from previous planning horizon.
"""

import logging

import numpy as np
import pandas as pd
import pypsa
import xarray as xr
from _helpers import (
    configure_logging,
    get_snapshots,
    set_scenario_config,
    update_config_from_wildcards,
)
from add_existing_baseyear import add_build_year_to_new_assets
from prepare_sector_network import get
from pypsa.clustering.spatial import normed_or_uniform

logger = logging.getLogger(__name__)
idx = pd.IndexSlice


def add_brownfield(n, n_p, year):
    logger.info(f"Preparing brownfield for the year {year}")

    # electric transmission grid set optimised capacities of previous as minimum
    n.lines.s_nom_min = n_p.lines.s_nom_opt
    dc_i = n.links[n.links.carrier == "DC"].index
    n.links.loc[dc_i, "p_nom_min"] = n_p.links.loc[dc_i, "p_nom_opt"]

    for c in n_p.iterate_components(["Link", "Generator", "Store"]):
        attr = "e" if c.name == "Store" else "p"

        # first, remove generators, links and stores that track
        # CO2 or global EU values since these are already in n
        n_p.remove(c.name, c.df.index[(c.df.lifetime == np.inf) &
                                       ~c.df.index.str.contains("existing")])

        # remove assets whose build_year + lifetime <= year
        n_p.remove(c.name, c.df.index[c.df.build_year + c.df.lifetime <= year])

        # remove assets if their optimized nominal capacity is lower than a threshold
        # since CHP heat Link is proportional to CHP electric Link, make sure threshold is compatible
        chp_heat = c.df.index[
            (c.df[f"{attr}_nom_extendable"] & c.df.index.str.contains("urban central"))
            & c.df.index.str.contains("CHP")
            & c.df.index.str.contains("heat")
        ]

        threshold = snakemake.params.threshold_capacity

        if not chp_heat.empty:
            threshold_chp_heat = (
                threshold
                * c.df.efficiency[chp_heat.str.replace("heat", "electric")].values
                * c.df.p_nom_ratio[chp_heat.str.replace("heat", "electric")].values
                / c.df.efficiency[chp_heat].values
            )
            n_p.remove(
                c.name,
                chp_heat[c.df.loc[chp_heat, f"{attr}_nom_opt"] < threshold_chp_heat],
            )

        n_p.remove(
            c.name,
            c.df.index[
                (c.df[f"{attr}_nom_extendable"] & ~c.df.index.isin(chp_heat))
                & (c.df[f"{attr}_nom_opt"] < threshold)
            ],
        )

        # copy over assets but fix their capacity
        c.df[f"{attr}_nom"] = c.df[f"{attr}_nom_opt"]
        c.df[f"{attr}_nom_extendable"] = False

        n.add(c.name, c.df.index, **c.df)

        # copy time-dependent
        selection = n.component_attrs[c.name].type.str.contains(
            "series"
        ) & n.component_attrs[c.name].status.str.contains("Input")
        for tattr in n.component_attrs[c.name].index[selection]:
            n.import_series_from_dataframe(c.pnl[tattr], c.name, tattr)

    # deal with gas network
    if snakemake.params.H2_retrofit:
        # subtract the already retrofitted from the maximum capacity
        h2_retrofitted_fixed_i = n.links[
            (n.links.carrier == "H2 pipeline retrofitted")
            & (n.links.build_year != year)
        ].index
        h2_retrofitted = n.links[
            (n.links.carrier == "H2 pipeline retrofitted")
            & (n.links.build_year == year)
        ].index

        # pipe capacity always set in prepare_sector_network to todays gas grid capacity * H2_per_CH4
        # and is therefore constant up to this point
        pipe_capacity = n.links.loc[h2_retrofitted, "p_nom_max"]
        # already retrofitted capacity from gas -> H2
        already_retrofitted = (
            n.links.loc[h2_retrofitted_fixed_i, "p_nom"]
            .rename(lambda x: x.split("-2")[0] + f"-{year}")
            .groupby(level=0)
            .sum()
        )
        remaining_capacity = pipe_capacity - already_retrofitted.reindex(
            index=pipe_capacity.index
        ).fillna(0)
        n.links.loc[h2_retrofitted, "p_nom_max"] = remaining_capacity

        # reduce gas network capacity
        gas_pipes_i = n.links[n.links.carrier == "gas pipeline"].index
        if not gas_pipes_i.empty:
            # subtract the already retrofitted from today's gas grid capacity
            pipe_capacity = n.links.loc[gas_pipes_i, "p_nom"]
            fr = "H2 pipeline retrofitted"
            to = "gas pipeline"
            CH4_per_H2 = 1 / snakemake.params.H2_retrofit_capacity_per_CH4
            already_retrofitted.index = already_retrofitted.index.str.replace(fr, to)
            remaining_capacity = (
                pipe_capacity
                - CH4_per_H2
                * already_retrofitted.reindex(index=pipe_capacity.index).fillna(0)
            )
            n.links.loc[gas_pipes_i, "p_nom"] = remaining_capacity
            n.links.loc[gas_pipes_i, "p_nom_max"] = remaining_capacity


def disable_grid_expansion_if_limit_hit(n):
    """
    Check if transmission expansion limit is already reached; then turn off.

    In particular, this function checks if the total transmission
    capital cost or volume implied by s_nom_min and p_nom_min are
    numerically close to the respective global limit set in
    n.global_constraints. If so, the nominal capacities are set to the
    minimum and extendable is turned off; the corresponding global
    constraint is then dropped.
    """
    types = {"expansion_cost": "capital_cost", "volume_expansion": "length"}
    for limit_type in types:
        glcs = n.global_constraints.query(f"type == 'transmission_{limit_type}_limit'")

        for name, glc in glcs.iterrows():
            total_expansion = (
                (
                    n.lines.query("s_nom_extendable")
                    .eval(f"s_nom_min * {types[limit_type]}")
                    .sum()
                )
                + (
                    n.links.query("carrier == 'DC' and p_nom_extendable")
                    .eval(f"p_nom_min * {types[limit_type]}")
                    .sum()
                )
            ).sum()

            # Allow small numerical differences
            if np.abs(glc.constant - total_expansion) / glc.constant < 1e-6:
                logger.info(
                    f"Transmission expansion {limit_type} is already reached, disabling expansion and limit"
                )
                extendable_acs = n.lines.query("s_nom_extendable").index
                n.lines.loc[extendable_acs, "s_nom_extendable"] = False
                n.lines.loc[extendable_acs, "s_nom"] = n.lines.loc[
                    extendable_acs, "s_nom_min"
                ]

                extendable_dcs = n.links.query(
                    "carrier == 'DC' and p_nom_extendable"
                ).index
                n.links.loc[extendable_dcs, "p_nom_extendable"] = False
                n.links.loc[extendable_dcs, "p_nom"] = n.links.loc[
                    extendable_dcs, "p_nom_min"
                ]

                n.global_constraints.drop(name, inplace=True)


def adjust_renewable_profiles(n, input_profiles, params, year):
    """
    Adjusts renewable profiles according to the renewable technology specified,
    using the latest year below or equal to the selected year.
    """

    # temporal clustering
    dr = get_snapshots(params["snapshots"], params["drop_leap_day"])
    snapshotmaps = (
        pd.Series(dr, index=dr).where(lambda x: x.isin(n.snapshots), pd.NA).ffill()
    )

    for carrier in params["carriers"]:
        if carrier == "hydro":
            continue

        with xr.open_dataset(getattr(input_profiles, "profile_" + carrier)) as ds:
            if ds.indexes["bus"].empty or "year" not in ds.indexes:
                continue

            closest_year = max(
                (y for y in ds.year.values if y <= year), default=min(ds.year.values)
            )

            p_max_pu = (
                ds["profile"]
                .sel(year=closest_year)
                .transpose("time", "bus")
                .to_pandas()
            )
            p_max_pu.columns = p_max_pu.columns + f" {carrier}"

            # temporal_clustering
            p_max_pu = p_max_pu.groupby(snapshotmaps).mean()

            # replace renewable time series
            n.generators_t.p_max_pu.loc[:, p_max_pu.columns] = p_max_pu


def update_heat_pump_efficiency(n: pypsa.Network, n_p: pypsa.Network, year: int):
    """
    Update the efficiency of heat pumps from previous years to current year
    (e.g. 2030 heat pumps receive 2040 heat pump COPs in 2030).

    Parameters
    ----------
    n : pypsa.Network
        The original network.
    n_p : pypsa.Network
        The network with the updated parameters.
    year : int
        The year for which the efficiency is being updated.

    Returns
    -------
    None
        This function updates the efficiency in place and does not return a value.
    """

    # get names of heat pumps in previous iteration
    heat_pump_idx_previous_iteration = n_p.links.index[
        n_p.links.index.str.contains("heat pump")
    ]
    # construct names of same-technology heat pumps in the current iteration
    corresponding_idx_this_iteration = heat_pump_idx_previous_iteration.str[:-4] + str(
        year
    )
    # update efficiency of heat pumps in previous iteration in-place to efficiency in this iteration
    n_p.links_t["efficiency"].loc[:, heat_pump_idx_previous_iteration] = (
        n.links_t["efficiency"].loc[:, corresponding_idx_this_iteration].values
    )


def adjust_transport(n, ref_year=2024):
    registrations = pd.read_csv(snakemake.input.car_registration, index_col=[0,1])
    for transport_type in ["light", "heavy"]:
        filter_links = (~n.links.p_nom_extendable & (n.links.lifetime==np.inf)
                        & (n.links.carrier == f"land transport oil {transport_type}"))
        links_i = n.links[filter_links].index
        
        factor = get(options["car_reg_factor"][transport_type], year)
        reg = registrations.loc[transport_type].iloc[:,0] * factor
        
        previous_year = n_p.links.build_year.max()
        unchanged_fleet = (1-(reg*(year-previous_year))).clip(lower=0)
        changed = unchanged_fleet.rename(index= lambda x: x + f" land transport oil {transport_type}-existing")
        n.links.loc[links_i, "p_nom"] = (n.links.loc[links_i, "p_nom"] * changed).fillna(0)
        
        # final = {}
        # for year in [2025, 2030, 2040, 2050]:
        #     unchanged_fleet = (1-(reg*(year-ref_year))).clip(lower=0)
        #     already_reduced =  (1-(reg*(previous_year-ref_year))).clip(lower=0)
        #     changed = (unchanged_fleet/already_reduced.replace(0,1)).rename(index= lambda x: x + f" land transport oil {transport_type}-existing")
        #     final[year] = (n.links.loc[links_i, "p_nom"] * changed).fillna(0)
        #     previous_year = year
                
            
        
    # remove very small capacity
    logger.info("Removing small land transport capacities")
    carriers = ['land transport EV heavy', 'land transport fuel cell heavy',
           'land transport oil heavy', 'land transport EV light',
           'land transport fuel cell light', 'land transport oil light']
    
    land_links = n.links[n.links.carrier.isin(carriers)]
    to_drop = land_links[(land_links.p_nom<1) & (~land_links.p_nom_extendable)]
    
    n.mremove("Link", to_drop.index)
    
    
def scale_transport_capacities(n, n_p):
    """
    if the total number of cars should stay constant but the demand increases or
    decreases, the vehicle km driven per car are changing. This requires an
    adjustment of the p_nom and capital_costs of the transport links.
    """
    logger.info("scaling transport capacities since constant number of vehicles assumed")
    load_p =  n_p.loads_t.p_set.sum().groupby(n_p.loads.carrier).sum()
    load = n.loads_t.p_set.sum().groupby(n.loads.carrier).sum()
    factor = load/load_p
    
    transport_types = ["light", "heavy"]
    for transport_type in transport_types:
        
        # links
        carrier = f"land transport demand {transport_type}"
        links_i = n.links[(
                            (n.links.bus1.map(n.buses.carrier)==carrier)    # all power engine types
                           |(n.links.carrier==f"BEV charger {transport_type}")   # BEV charger
                           |(n.links.bus0.str.contains(f"EV battery {transport_type}"))  # V2G
                           )
                          &(~n.links.p_nom_extendable)].index
        n.links.loc[links_i, "p_nom"] *= factor.loc[carrier]
        n.links.loc[links_i, "p_nom_opt"] *= factor.loc[carrier]
        n.links.loc[links_i, "capital_cost"] /= factor.loc[carrier]
        
        
        # stores
        bev_dsm_i = n.stores[
            (n.stores.index.str.contains(f"battery storage {transport_type}")
              & (~n.stores.e_nom_extendable))
        ].index
        n.stores.loc[bev_dsm_i, "e_nom"] *= factor
        n.stores.loc[bev_dsm_i, "e_nom_opt"] *= factor
        
#%%
if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake(
            "add_brownfield",
            clusters="39",
            configfiles="/home/lisa/Documents/playground/pypsa-eur/config/config.transport_zecm_mocksnakemake.yaml",
            opts="",
            ll="v1.0",
            sector_opts="",
            planning_horizons=2030,
        )

    configure_logging(snakemake)
    set_scenario_config(snakemake)

    update_config_from_wildcards(snakemake.config, snakemake.wildcards)

    logger.info(f"Preparing brownfield from the file {snakemake.input.network_p}")

    year = int(snakemake.wildcards.planning_horizons)
    
    options = snakemake.params.sector

    n = pypsa.Network(snakemake.input.network)

    adjust_renewable_profiles(n, snakemake.input, snakemake.params, year)

    add_build_year_to_new_assets(n, year)

    n_p = pypsa.Network(snakemake.input.network_p)

    update_heat_pump_efficiency(n, n_p, year)

    add_brownfield(n, n_p, year)

    disable_grid_expansion_if_limit_hit(n)
    
    if options["endogenous_transport"]:
        scale_transport_capacities(n, n_p)
        adjust_transport(n)

    n.meta = dict(snakemake.config, **dict(wildcards=dict(snakemake.wildcards)))
    n.export_to_netcdf(snakemake.output[0])
