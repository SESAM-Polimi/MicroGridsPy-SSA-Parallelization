"""A household cluster must carry a valid cooling class (Nov 2026 fix).

Run from the repo root:  python -m pytest tests/test_cooling_class.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from mgpy2.config_map import ThesisConfig
from mgpy2.input_prep import PrepConfig, compute_demand_kwh, cooling_regime, year_labels


@pytest.mark.parametrize("value", ["NC", "AY", "OM", "AS", " AS "])
def test_valid_classes_pass(value):
    assert cooling_regime({"cooling": value}, households=10) == value.strip()


@pytest.mark.parametrize("value", ["NA", "", None, float("nan"), "3", "Aw", "nc"])
def test_household_cluster_without_a_valid_class_stops(value):
    with pytest.raises(ValueError, match="cooling class"):
        cooling_regime({"cat": "GHSL_1", "cooling": value}, households=10)


def test_school_only_cluster_needs_no_class():
    assert cooling_regime({"cat": "SCHOOL_1", "cooling": "NA"}, households=0) == "NC"


def test_old_fallback_only_on_request_and_only_for_missing_values():
    assert cooling_regime({"cooling": "NA"}, households=10, allow_missing=True) == "NC"
    with pytest.raises(ValueError):            # a wrong code is never read as "no cooling"
        cooling_regime({"cooling": "Aw"}, households=10, allow_missing=True)


def test_pipeline_default_is_strict_and_reaches_the_demand():
    assert ThesisConfig().allow_missing_cooling is False
    row = {"cat": "GHSL_1", "lat": 5.0, "cooling": "NA", "h_tier2": 100, "school_total_demand": 0.0}
    cfg = dict(years=2, year_labels=year_labels(2025, 2), demand_growth=0.03, demand_growth_mode="consistent")
    with pytest.raises(ValueError, match="GHSL_1"):
        compute_demand_kwh(row, PrepConfig(**cfg))
    old = compute_demand_kwh(row, PrepConfig(**cfg, allow_missing_cooling=True))
    nc = compute_demand_kwh(row | {"cooling": "NC"}, PrepConfig(**cfg))
    np.testing.assert_array_equal(old, nc)
