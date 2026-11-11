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


def year(value):
    return np.full((1, 12), float(value))


@pytest.mark.parametrize("tmin, tavg, tmax, per_day", [
    (15, 20, 25, 0.0),                      # never above 26
    (18, 24, 30, (30 - 26) / 4),            # hot afternoons only: 1.0
    (22, 28, 34, (34 - 26) / 2 - (26 - 22) / 4),   # warm days, cool nights: 3.0
    (27, 31, 35, 31 - 26),                  # always above 26: 5.0
])
def test_met_office_cases(tmin, tavg, tmax, per_day):
    cdd = monthly_cdd(year(tmin), year(tavg), year(tmax))
    np.testing.assert_allclose(cdd[0], per_day * DAYS_PER_MONTH)


def test_mean_below_base_still_counts_hot_afternoons():
    """The case a mean-only CDD misses: Tavg 25 C but Tmax 39 C."""
    cdd = monthly_cdd(year(13), year(25), year(39))
    assert cdd[0].mean() > 90                # (39 - 26) / 4 = 3.25 per day
    assert cooling_class(cdd)[0] == "AY"


def test_classes_follow_the_half_years():
    hot, mild = 5.0 * DAYS_PER_MONTH, 1.0 * DAYS_PER_MONTH
    apr_sep = np.isin(np.arange(12), [3, 4, 5, 6, 7, 8])
    cdd = np.vstack([hot, np.where(apr_sep, hot, mild), np.where(apr_sep, mild, hot), mild])
    assert cooling_class(cdd).tolist() == ["AY", "AS", "OM", "NC"]
