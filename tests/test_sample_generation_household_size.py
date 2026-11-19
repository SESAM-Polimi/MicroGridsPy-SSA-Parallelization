"""Household size table read by the sample generator (sample_generation/input_generator.py).

Run from the repo root:  python -m pytest tests/test_sample_generation_household_size.py -q
Skipped where the generator's GIS dependencies are not installed (e.g. the cluster).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

for module in ("geopandas", "rasterio", "fiona", "scipy", "shapely"):
    pytest.importorskip(module)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "sample_generation"))
from input_generator import allocate_households, load_country_tables   # noqa: E402

TABLE = REPO / "sample_generation" / "tables" / "household_size_undesa2022.csv"


def test_every_sample_country_has_a_sourced_size():
    table = pd.read_csv(TABLE).set_index("iso3")
    samples = {p.parent.name for p in (REPO / "data" / "sample_input_2025").glob("*/advanced_sample.csv")}
    assert samples | {"SLE"} <= set(table.index)
    assert table.index.is_unique
    # every value carries its survey type and year, so it can be cited
    assert table[["reference_year", "data_source"]].notna().all().all()


def test_sizes_are_plausible():
    """Sub-Saharan national means lie between about 3 and 9 persons (UN DESA 2022: 3.36-8.66)."""
    assert pd.read_csv(TABLE)["household_size"].between(3.0, 9.0).all()


def test_generator_reads_this_table():
    _, sizes = load_country_tables()
    assert sizes == pd.read_csv(TABLE).set_index("iso3")["household_size"].to_dict()


def test_tiers_add_up_to_the_households():
    for households in (0, 1, 7, 128, 5000):
        tiers = allocate_households(rwi=0.3, rwi_std=0.8, households=households, adjustment=-0.2)
        assert len(tiers) == 5 and min(tiers) >= 0
        assert sum(tiers) == households
    assert np.isclose(sum(allocate_households(-1.0, 0.05, 40, 0.0)), 40)    # std floored at 0.1
