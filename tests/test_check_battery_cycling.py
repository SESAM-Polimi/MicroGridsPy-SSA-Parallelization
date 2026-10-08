"""hpc/check_battery_cycling.py: realised cycles from dispatch vs the assumed cycling.

Run from the repo root:  python -m pytest tests/test_check_battery_cycling.py -q
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_battery_cycling", Path(__file__).resolve().parents[1] / "hpc" / "check_battery_cycling.py")
check_battery_cycling = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_battery_cycling)


def test_realised_cycles_from_dispatch(tmp_path):
    """10 kWh nameplate, DoD 0.8, eta 0.9: 7.2 kWh AC out per day for 2 days = 2 cycles."""
    res = tmp_path / "projects" / "GHSL_1" / "results"
    res.mkdir(parents=True)
    res.joinpath("summary.json").write_text(json.dumps({
        "capacity_by_year": [{"year": "2026", "battery_kwh": 10.0}, {"year": "2027", "battery_kwh": 10.0}],
        "meta": {"run": {
            "pipeline_config": {"battery_discharge_efficiency": 0.9, "battery_depth_of_discharge": 0.8},
            "battery_degradation": {"cycling_estimate": "horizon_mean", "calendar_lifetime_years_used": 9.0,
                                    "assumed_equivalent_full_cycles_per_year": 3.0}}},
    }))
    hours = 48
    discharge_2026 = [0.6] * 12 + [0.0] * 12 + [0.6] * 12 + [0.0] * 12      # 14.4 kWh AC
    discharge_2027 = [x * 1.5 for x in discharge_2026]                       # 21.6 kWh AC
    pd.DataFrame({"period": list(range(hours)) * 2, "year": [2026] * hours + [2027] * hours,
                  "scenario": "s1", "battery_discharge": discharge_2026 + discharge_2027}).to_csv(
        res / "dispatch.csv", index=False)
    row = check_battery_cycling.check_cluster(res)
    assert row["realised_efc_first_year"] == pytest.approx(14.4 / 0.9 / 8.0)    # 2.0
    assert row["realised_efc_last_year"] == pytest.approx(21.6 / 0.9 / 8.0)     # 3.0
    assert row["realised_efc_mean"] == pytest.approx(2.5)
    assert row["realised_over_assumed"] == pytest.approx(2.5 / 3.0)


def test_cycling_check_without_dispatch_says_so(tmp_path):
    res = tmp_path / "projects" / "GHSL_1" / "results"
    res.mkdir(parents=True)
    res.joinpath("summary.json").write_text(json.dumps({"meta": {"run": {}}}))
    assert "DISPATCH_FORMAT" in check_battery_cycling.check_cluster(res)["note"]
