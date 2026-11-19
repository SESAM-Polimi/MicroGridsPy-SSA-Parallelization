"""Empty sample cells read as zero in the demand step (input_prep.compute_demand_kwh).

The regenerated samples (input_generator, Nov 2026) leave the household and facility columns
of school rows empty; pandas reads them as float NaN. Before this test, NaN reached the
household count and every school stopped at the cooling check.

Run from the repo root:  python -m pytest tests/test_empty_cells.py -q
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from mgpy2.config_map import ThesisConfig
from mgpy2.input_prep import PrepConfig, compute_demand_kwh

HEADER = ("cat,school_fid,h_tier1,h_tier2,h_tier3,h_tier4,h_tier5,hospital_1,hospital_2,hospital_3,"
          "hospital_4,hospital_5,schools,school_total_demand,lat,lon,distance_grid_km,pop_sum,density,"
          "cooling,rwi,rwi_std")
# first school of the regenerated AGO sample, as written by the generator
SCHOOL_ROW = "SCHOOL_1,1,,,,,,,,,,,1,840000.0,-18.038889999999995,20.84333,,,,NA,,"
# the same school as the 2025 samples wrote it (explicit zeros)
SCHOOL_ROW_ZEROS = "SCHOOL_1,1,0,0,0,0,0,0,0,0,0,0,1,840000.0,-18.038889999999995,20.84333,0.0,0.0,0.0,NA,0.0,0.0"


def _prep():
    cfg = ThesisConfig()
    return PrepConfig(years=cfg.horizon(), year_labels=cfg.year_labels(), demand_growth=cfg.demand_growth,
                      demand_growth_mode=cfg.demand_growth_mode)


def _row(line):
    """Read the line exactly as load_sample does (pandas), so empty cells become NaN."""
    return pd.read_csv(io.StringIO(HEADER + "\n" + line)).iloc[0].to_dict()


def test_school_with_empty_cells_gets_its_demand():
    row = _row(SCHOOL_ROW)
    assert np.isnan(row["h_tier1"])                       # what the generator writes
    demand = compute_demand_kwh(row, _prep())
    assert np.isfinite(demand).all() and demand.sum() > 0


def test_empty_cells_equal_explicit_zeros():
    a = compute_demand_kwh(_row(SCHOOL_ROW), _prep())
    b = compute_demand_kwh(_row(SCHOOL_ROW_ZEROS), _prep())
    np.testing.assert_array_equal(a, b)
