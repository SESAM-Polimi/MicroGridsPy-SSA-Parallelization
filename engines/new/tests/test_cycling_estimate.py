"""Cycling estimate behind the derived battery life (data.py, _estimate_equivalent_full_cycles_per_year).

Synthetic profiles: 2 days per "year", PV only from 06:00 to 18:00, a night load that grows by
a fixed rate every year. Small enough to check every number by hand.

Run from engines/new:  python -m pytest tests/test_cycling_estimate.py -q
"""
from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from core.data_pipeline.battery_degradation_model import get_battery_degradation_settings
from core.multi_year_model.data import _estimate_equivalent_full_cycles_per_year

YEARS = [2026, 2027, 2028, 2029]
GROWTH = 0.10
SOH_EOL = 0.8
HOURS = 48
DAY = np.r_[np.ones(6), np.zeros(12), np.ones(6)]          # 1 = dark hour


def _inputs(growth_by_year=None):
    """Night load 1 kWh/h on day 1 and 2 kWh/h on day 2, scaled by (1+g)^year."""
    growth_by_year = growth_by_year or [(1 + GROWTH) ** i for i in range(len(YEARS))]
    base = np.r_[np.ones(24), 2 * np.ones(24)] * np.tile(DAY, 2)
    load = np.stack([base * f for f in growth_by_year])[:, :, None]            # year, period, scenario
    sun = np.tile(1.0 - np.tile(DAY, 2), (len(YEARS), 1))[:, :, None, None]    # year, period, scenario, resource
    coords = {"year": YEARS, "period": np.arange(HOURS), "scenario": ["s1"]}
    load_da = xr.DataArray(load, dims=("year", "period", "scenario"), coords=coords)
    sun_da = xr.DataArray(sun, dims=("year", "period", "scenario", "resource"),
                          coords={**coords, "resource": ["Solar"]})
    return load_da, sun_da


def _design_night(y_index, growth_by_year=None):
    """95th percentile of the two nightly totals (12 and 24 kWh in year 0)."""
    growth_by_year = growth_by_year or [(1 + GROWTH) ** i for i in range(len(YEARS))]
    return float(np.percentile([12.0, 24.0], 95.0)) * growth_by_year[y_index]


def _efc(mode, sizing_year_of=None, growth_by_year=None):
    load, sun = _inputs(growth_by_year)
    efc, profile = _estimate_equivalent_full_cycles_per_year(
        load_demand=load, resource_availability=sun, end_of_life_soh=SOH_EOL,
        sizing_year_of=sizing_year_of, mode=mode)
    assert profile.sizes == {"period": HOURS}
    return efc


def test_first_year_is_the_original_formula():
    expected = 36.0 * SOH_EOL / _design_night(0)                # annual dark load 36 kWh in year 0
    assert _efc("first_year") == pytest.approx(expected)


def test_horizon_mean_uses_the_nameplate_of_the_last_year():
    # single investment step: every year is served by a battery sized for 2029
    per_year = [36.0 * (1 + GROWTH) ** i * SOH_EOL / _design_night(3) for i in range(len(YEARS))]
    assert _efc("horizon_mean") == pytest.approx(np.mean(per_year))


def test_with_uniform_growth_first_year_equals_the_peak():
    """The old formula is the cycling of the sizing year itself; the mean is lower by the growth."""
    peak, mean = _efc("first_year"), _efc("horizon_mean")
    factor = np.mean([(1 + GROWTH) ** (i - 3) for i in range(len(YEARS))])
    assert mean == pytest.approx(peak * factor)
    assert mean < peak


def test_without_growth_the_two_estimates_agree():
    flat = [1.0] * len(YEARS)
    assert _efc("horizon_mean", growth_by_year=flat) == pytest.approx(_efc("first_year", growth_by_year=flat))


def test_capacity_expansion_sizes_each_step_for_its_own_last_year():
    # two investment steps: 2026-2027 sized for 2027, 2028-2029 sized for 2029
    sizing = {2026: 2027, 2027: 2027, 2028: 2029, 2029: 2029}
    per_year = [36.0 * (1 + GROWTH) ** i * SOH_EOL / _design_night({0: 1, 1: 1, 2: 3, 3: 3}[i])
                for i in range(len(YEARS))]
    assert _efc("horizon_mean", sizing_year_of=sizing) == pytest.approx(np.mean(per_year))
    assert _efc("horizon_mean", sizing_year_of=sizing) > _efc("horizon_mean")


def test_unknown_mode_is_refused():
    with pytest.raises(Exception, match="cycling_estimate"):
        _efc("average")


def test_settings_default_and_validation():
    form = {"core_formulation": "dynamic",
            "battery_model": {"degradation_model": {"coefficients_enabled": True}}}
    assert get_battery_degradation_settings(form, battery_loss_model="constant_efficiency")[
        "cycling_estimate"] == "horizon_mean"
    form["battery_model"]["degradation_model"]["cycling_estimate"] = "first_year"
    assert get_battery_degradation_settings(form, battery_loss_model="constant_efficiency")[
        "cycling_estimate"] == "first_year"
    form["battery_model"]["degradation_model"]["cycling_estimate"] = "peak"
    with pytest.raises(Exception, match="cycling_estimate"):
        get_battery_degradation_settings(form, battery_loss_model="constant_efficiency")
