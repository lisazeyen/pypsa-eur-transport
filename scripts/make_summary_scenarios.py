#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu May 23 10:26:59 2024

@author: lisa
"""

import pandas as pd
import os
import logging
import matplotlib.pyplot as plt
from plot_summary import rename_techs

logger = logging.getLogger(__name__)

def get_folder_names(path):
    try:
        # List all items in the given directory
        items = os.listdir(path)
        # Filter out only directories
        folders = [item for item in items if os.path.isdir(os.path.join(path, item))]
        return folders
    except Exception as e:
        print(f"An error occurred: {e}")
        return []
    

path = "/home/lisa/Documents/pypsa-eur/results/test-shipping-3-3h/"

scenarios = get_folder_names(path)

total_costs = {}
supply_energy = {}
for scenario in scenarios:
    try:
        total_costs[scenario] = pd.read_csv(path+scenario+"/csvs/costs.csv",
                                            index_col=[0,1,2],
                                            header=[0,1,2,3])
        supply_energy[scenario]  = pd.read_csv(path+scenario+"/csvs/supply_energy.csv",
                                            index_col=[0,1,2],
                                            header=[0,1,2,3])
    except FileNotFoundError:
        logger.info(f"{scenario} not solved yet.")
        
costs_sumed = pd.concat(total_costs, axis=1).sum().droplevel([1,2,3])
costs_sumed = costs_sumed[costs_sumed!=0]

a = costs_sumed.unstack().T
# List of 10 different line styles
line_styles = ['-', '--', '-.', ':', '-o', '--s', '-.d', ':^', '-+', '--*']
column_styles = line_styles[:len(a.columns)]

(a/1e9).plot(style=column_styles)
plt.ylabel("Total system costs \n [billion Euro/year]")
plt.legend(bbox_to_anchor=(1,1))
plt.grid()

plt.savefig(path+"together/costs.pdf", bbox_inches="tight")

#%%
for carrier in ["shipping", 'land transport demand heavy', 'land transport demand light']:
    total = pd.concat(supply_energy, axis=1).T.droplevel([1,2,3])[carrier].T.droplevel(0).dropna(axis=1)
    total = total.rename(index=lambda x: x.replace("1", ""))
    total = total.stack(-2)
    total.rename(columns = lambda x: int(x))
    total.index = total.index.swaplevel()
    
    a = total.T 
    grouped = total.T.stack().stack()
    
    ncols=2
    fig, ax = plt.subplots(nrows=round(len(a.columns.levels[0])/ncols),
                            ncols=ncols, 
                            figsize=(12, 9),
                            sharey=True,
                            sharex=True)
    fig.suptitle(carrier, fontsize=16)
    for i, scenario in enumerate(a.columns.levels[0]):
        row = i // ncols
        col = i % ncols
        df = a[scenario]
        df = df[df>0].dropna(axis=1)
        ls = line_styles[:(len(df.columns))]
        b = (df.div(df.sum(axis=1), axis=0)*100)
        b.plot(title=scenario,
                ax=ax[row][col], color=[snakemake.config["plotting"]["tech_colors"][carrier] for 
                                        carrier in b.columns],
                style=ls,
                lw=2,
                legend=False)
        ax[row][col].set_ylim([-5,105])
        ax[row][col].set_xlabel("year")
        ax[row][col].set_ylabel("share [%]")
        ax[row][col].grid(axis='y')
    plt.legend(bbox_to_anchor=(1,1))
    plt.savefig(path+f"together/supply_energy_share_{carrier}.pdf", bbox_inches="tight")
#%%
df = (pd.concat(total_costs, axis=1).droplevel([1,2,3], axis=1)
        .groupby(level=2).sum().xs("2040", level=1, axis=1))  
df = df.groupby(df.index.map(rename_techs)).sum()
to_drop = df.index[df.max(axis=1) < 1]

logger.info(
    f"Dropping technology with costs below 1 EUR billion per year"
)
logger.debug(df.loc[to_drop])

df = df.drop(to_drop)


