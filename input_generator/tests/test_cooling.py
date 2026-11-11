"""Cooling degree days and classes of the input generator.

Run from input_generator/:  python -m pytest tests/test_cooling.py -q
(needs the generator's own dependencies: geopandas, rasterio, fiona, scipy)
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from input_generator import DAYS_PER_MONTH, cooling_class, monthly_cdd   # noqa: E402

BASE = 26.0


def year(value):
    return np.full((1, 12), float(value))


def per_day(tmin, tmax):
    """CDD per day for one (Tmin, Tmax) pair, read back from January (31 days)."""
    return monthly_cdd(year(tmin), year(tmax))[0, 0] / 31


@pytest.mark.parametrize("tmin, tmax, expected", [
    (15, 25, 0.0),                                  # never above 26
    (18, 30, (30 - 26) / 4),                        # Tmid 24: hot afternoons only, 1.0
    (22, 34, (34 - 26) / 2 - (26 - 22) / 4),        # Tmid 28: warm days, cool nights, 3.0
    (27, 35, 31 - 26),                              # Tmin above 26: Tmid - 26 = 5.0
])
def test_met_office_cases(tmin, tmax, expected):
    cdd = monthly_cdd(year(tmin), year(tmax))
    np.testing.assert_allclose(cdd[0], expected * DAYS_PER_MONTH)


@pytest.mark.parametrize("tmin, tmax", [
    (10, BASE),            # Tmax = base: case 1 | case 2
    (BASE - 6, BASE + 6),  # Tmid = base: case 2 | case 3
    (BASE, 38),            # Tmin = base: case 3 | case 4
])
def test_no_jump_at_the_case_boundaries(tmin, tmax):
    """With the midpoint as daily mean the four cases join continuously.

    Shift the pair by +-eps so each side falls in a different case; the values must meet.
    """
    eps = 1e-6
    below, above = per_day(tmin - eps, tmax - eps), per_day(tmin + eps, tmax + eps)
    assert above == pytest.approx(below, abs=1e-5)


def test_mean_below_base_still_counts_hot_afternoons():
    """The case a mean-only CDD misses: Tmid 25 C but Tmax 38 C."""
    cdd = monthly_cdd(year(12), year(38))
    assert cdd[0].mean() > 90                # (38 - 26) / 4 = 3.0 per day
    assert cooling_class(cdd)[0] == "AY"


def test_classes_follow_the_half_years():
    hot, mild = 5.0 * DAYS_PER_MONTH, 1.0 * DAYS_PER_MONTH
    apr_sep = np.isin(np.arange(12), [3, 4, 5, 6, 7, 8])
    cdd = np.vstack([hot, np.where(apr_sep, hot, mild), np.where(apr_sep, mild, hot), mild])
    assert cooling_class(cdd).tolist() == ["AY", "AS", "OM", "NC"]
