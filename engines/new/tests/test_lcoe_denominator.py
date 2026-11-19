"""LCOE denominator of the multi-year export (core/export/multi_year_core.build_summary_metrics).

LCOE = NPC / sum_y served_y / (1 + r)^y, with y = 1..N. With flat demand and nothing lost, the
denominator is the annual energy times the annuity factor sum_{y=1..N} (1 + r)^-y, which is
8.5136 for r = 10 %, N = 20. The old engine counted year 20 undiscounted (off by one); this test
fails if the discounting ever starts at year 0 or stops a year early or late (checklist C11).

Run from engines/new:  python -m pytest tests/test_lcoe_denominator.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import core.export.multi_year_core as core_export

YEARS = [str(2026 + i) for i in range(20)]
ANNUAL_KWH = 1000.0


def annuity_factor(rate: float, years: int) -> float:
    return (1.0 - (1.0 + rate) ** -years) / rate


def _metrics(monkeypatch, rate, lost_share=0.0, scenarios=("s1",)):
    # the investment table is irrelevant to the denominator and needs a solved model: stub it
    monkeypatch.setattr(core_export, "build_investment_summary_table_multi_year",
                        lambda **_: pd.DataFrame({"Nominal investment cost": [0.0],
                                                  "Present-value investment cost": [0.0]}))
    sets = xr.Dataset(coords={"year": YEARS, "scenario": list(scenarios)})
    data = xr.Dataset(attrs={"settings": {"social_discount_rate": rate}})
    rows = [{"year": y, "scenario": s, "load_demand": ANNUAL_KWH / 24, "lost_load": lost_share * ANNUAL_KWH / 24}
            for y in YEARS for s in scenarios for _ in range(24)]
    npc = 1234.5
    out = core_export.build_summary_metrics(sets=sets, data=data, design_df=pd.DataFrame(),
                                            dispatch_df=pd.DataFrame(rows), objective_value=npc)
    return out, npc


def test_annuity_factor_value():
    assert annuity_factor(0.10, 20) == pytest.approx(8.5136, abs=1e-4)


def test_flat_demand_denominator_is_the_annuity(monkeypatch):
    out, npc = _metrics(monkeypatch, rate=0.10)
    assert out["served_energy_discounted_kwh"] == pytest.approx(ANNUAL_KWH * annuity_factor(0.10, 20))
    assert out["lcoe_per_kwh"] == pytest.approx(npc / (ANNUAL_KWH * 8.513564), rel=1e-6)


def test_not_off_by_one(monkeypatch):
    """The usual slips give clearly different numbers at 10 %, 20 years:
    exponents 0..19 (first year undiscounted) 9.3649, 19 years 8.3649, 21 years 8.6487."""
    out, _ = _metrics(monkeypatch, rate=0.10)
    denominator = out["served_energy_discounted_kwh"] / ANNUAL_KWH
    for wrong in (sum(1.1 ** -y for y in range(0, 20)),
                  annuity_factor(0.10, 19),
                  annuity_factor(0.10, 21)):
        assert abs(denominator - wrong) > 0.1


def test_lost_load_is_not_served_energy(monkeypatch):
    out, _ = _metrics(monkeypatch, rate=0.10, lost_share=0.25)
    assert out["served_energy_discounted_kwh"] == pytest.approx(0.75 * ANNUAL_KWH * annuity_factor(0.10, 20))


def test_equal_weight_scenarios_average(monkeypatch):
    out, _ = _metrics(monkeypatch, rate=0.10, scenarios=("s1", "s2"))
    assert out["served_energy_discounted_kwh"] == pytest.approx(ANNUAL_KWH * annuity_factor(0.10, 20))
