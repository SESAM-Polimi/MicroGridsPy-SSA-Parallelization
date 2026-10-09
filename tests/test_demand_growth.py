"""Demand growth (checklist C01): 3 %/yr compound growth for every user is the default.

Run from the repo root:  python -m pytest tests/test_demand_growth.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from mgpy2.config_map import ThesisConfig
from mgpy2.input_prep import PrepConfig, compute_demand_kwh, year_labels

YEARS = 20
NO_USERS = {f"h_tier{t}": 0 for t in range(1, 6)} | {f"hospital_{t}": 0 for t in range(1, 6)}
USERS = {   # one cluster per user type, so each growth path is checked on its own
    "households": NO_USERS | {"h_tier2": 150, "h_tier3": 40},
    "health facility": NO_USERS | {"hospital_2": 1},
    "school": NO_USERS | {"school_total_demand": 12_000.0},   # Wh/yr in year 1
}


def _demand(users: dict, mode: str) -> np.ndarray:
    row = {"lat": 5.0, "cooling": "AY", "school_total_demand": 0.0} | users
    cfg = PrepConfig(years=YEARS, year_labels=year_labels(2025, YEARS),
                     demand_growth=0.03, demand_growth_mode=mode)
    return compute_demand_kwh(row, cfg)


def test_pipeline_default_is_consistent_growth():
    assert ThesisConfig().demand_growth_mode == "consistent"
    assert ThesisConfig().demand_growth == pytest.approx(0.03)


@pytest.mark.parametrize("kind", USERS)
def test_every_user_grows_three_percent_per_year(kind):
    annual = _demand(USERS[kind], "consistent").sum(axis=0)
    np.testing.assert_allclose(annual / annual[0], 1.03 ** np.arange(YEARS), rtol=1e-12)
    assert annual[-1] / annual[0] == pytest.approx(1.03 ** 19)


@pytest.mark.parametrize("kind", USERS)
def test_year_one_is_the_same_in_both_modes(kind):
    np.testing.assert_allclose(_demand(USERS[kind], "consistent")[:, 0],
                               _demand(USERS[kind], "thesis_faithful")[:, 0], rtol=0, atol=0)


def test_mode_must_be_given_and_spelled_right():
    with pytest.raises(TypeError):                      # no silent default any more
        PrepConfig(years=YEARS, year_labels=year_labels(2025, YEARS), demand_growth=0.03)
    with pytest.raises(ValueError, match="demand_growth_mode"):
        PrepConfig(years=YEARS, year_labels=year_labels(2025, YEARS), demand_growth=0.03,
                   demand_growth_mode="consistant")
