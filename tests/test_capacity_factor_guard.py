"""mgpy2/input_prep.check_capacity_factor: plausibility of the solar input (checklist C15).

Run from the repo root:  python -m pytest tests/test_capacity_factor_guard.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from mgpy2.input_prep import PV_CF_MEAN_MAX, PV_CF_MEAN_MIN, check_capacity_factor

HOURS = 8760


def _sunny_year(peak=0.9):
    """Half-sine from 06:00 to 18:00 every day: annual mean = peak * 12 * (2/pi) / 24 ~ 0.29 * peak."""
    hour = np.arange(HOURS) % 24
    day = np.clip(np.sin(np.pi * (hour - 6) / 12), 0.0, None)
    return peak * day


def test_realistic_year_passes():
    cf = _sunny_year(0.7)
    assert PV_CF_MEAN_MIN < cf.mean() < PV_CF_MEAN_MAX        # ~0.2, like the PVGIS sites
    check_capacity_factor(cf, lat=0.0, lon=0.0)


def test_hours_above_nameplate_are_allowed():
    """Cold clear highland hours (Maseru: 8 hours, peak 1.04) must not stop the cluster."""
    cf = _sunny_year(0.7)
    cf[12] = 1.04
    check_capacity_factor(cf, lat=-29.3, lon=27.5)


@pytest.mark.parametrize("scale", [1000.0, 1 / 1000.0], ids=["W read as kW", "kW read as W"])
def test_unit_errors_are_refused(scale):
    with pytest.raises(ValueError, match="outside the plausible range"):
        check_capacity_factor(_sunny_year(0.7) * scale, lat=0.0, lon=0.0)


@pytest.mark.parametrize("bad", [np.zeros(HOURS), np.full(HOURS, np.nan)])
def test_no_sun_or_missing_data_is_refused(bad):
    with pytest.raises(ValueError, match="no usable solar output"):
        check_capacity_factor(bad, lat=0.0, lon=0.0)
