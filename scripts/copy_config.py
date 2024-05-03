
from shutil import copy


files = {
    "config.yaml": "config.yaml",
    "scripts/solve_network.py": "solve_network.py",
    "scripts/prepare_sector_network.py": "prepare_sector_network.py",
}

if __name__ == '__main__':
    if 'snakemake' not in globals():
        from _helpers import mock_snakemake
        configfiles="/home/lisa/mnt/pypsa-eur/config/config (copy).yaml",
        snakemake = mock_snakemake('copy_config')

    basepath =  snakemake.output.split("config.yaml")[0]

    for f, name in files.items():
        copy(f, basepath + name)

    # with open(basepath + 'config.snakemake.yaml', 'w') as yaml_file:
    #     yaml.dump(
    #         snakemake.config,
    #         yaml_file,
    #         default_flow_style=False,
    #         allow_unicode=True,
    #         sort_keys=False
    #     )