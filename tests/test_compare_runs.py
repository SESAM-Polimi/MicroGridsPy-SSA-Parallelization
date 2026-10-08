"""hpc/compare_runs.py: reference given as a table or as another run's projects folder.

Run from the repo root:  python -m pytest tests/test_compare_runs.py -q
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "compare_runs", Path(__file__).resolve().parents[1] / "hpc" / "compare_runs.py")
compare_runs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(compare_runs)


def _summary(projects: Path, cat: str, *, npc: float, energy: float, bat: float, solve: float) -> None:
    """Write the fields compare_runs reads into <projects>/<cat>/results/summary.json."""
    res = projects / cat / "results"
    res.mkdir(parents=True)
    res.joinpath("summary.json").write_text(json.dumps({
        "metrics": {"objective_npc": npc, "lcoe_per_kwh": npc / energy,
                    "served_energy_discounted_kwh": energy},
        "design_by_step": [{"technology": "renewable", "installed_capacity": 10.0},
                           {"technology": "battery", "installed_capacity": bat}],
        "meta": {"run": {"solve_seconds": solve, "code_version": "abc1234"}},
    }))


def test_folder_reference_matches_table_reference(tmp_path):
    old, new = tmp_path / "old" / "projects", tmp_path / "new" / "projects"
    _summary(old, "GHSL_1", npc=1000.0, energy=500.0, bat=40.0, solve=1200.0)
    _summary(old, "SCHOOL_2", npc=2000.0, energy=800.0, bat=60.0, solve=900.0)
    _summary(new, "GHSL_1", npc=1010.0, energy=500.0, bat=41.0, solve=100.0)
    _summary(new, "SCHOOL_2", npc=1990.0, energy=800.0, bat=59.0, solve=90.0)

    new_table, errors = compare_runs.collect(new)
    assert errors == {}
    both = compare_runs.compare(new_table, compare_runs.load_reference(old))
    both = both.set_index("cat")
    assert both.loc["GHSL_1", "npc_ratio"] == pytest.approx(1.01)
    assert both.loc["SCHOOL_2", "bat_kwh_ratio"] == pytest.approx(59.0 / 60.0)
    assert both.loc["GHSL_1", "solve_s_ratio"] == pytest.approx(100.0 / 1200.0)
    assert (both["energy_ratio"] == 1.0).all()

    # the same reference written as a compare_table.csv gives the same ratios
    table = tmp_path / "compare_table.csv"
    pd.DataFrame({"cat": ["GHSL_1", "SCHOOL_2"], "sep_objective": [1000.0, 2000.0],
                  "sep_lcoe": [2.0, 2.5], "served_energy_disc_kwh": [500.0, 800.0],
                  "cap_solar": [10.0, 10.0], "cap_battery": [40.0, 60.0],
                  "sep_solve_s": [1200.0, 900.0]}).to_csv(table, index=False)
    both_csv = compare_runs.compare(new_table, compare_runs.load_reference(table)).set_index("cat")
    pd.testing.assert_series_equal(both["npc_ratio"], both_csv["npc_ratio"])


def test_failed_clusters_are_reported_not_joined(tmp_path):
    new = tmp_path / "projects"
    _summary(new, "GHSL_1", npc=1000.0, energy=500.0, bat=40.0, solve=100.0)
    err = new / "GHSL_3" / "results"
    err.mkdir(parents=True)
    err.joinpath("error.txt").write_text("GurobiError: Out of memory\ntraceback...")
    table, errors = compare_runs.collect(new)
    assert list(table["cat"]) == ["GHSL_1"]
    assert errors == {"GHSL_3": "GurobiError: Out of memory"}


def test_empty_reference_folder_stops(tmp_path):
    (tmp_path / "projects").mkdir()
    with pytest.raises(SystemExit):
        compare_runs.load_reference(tmp_path / "projects")


def test_september_sweep_columns_are_understood(tmp_path):
    """eth_sep2026_results.csv already uses npc/lcoe/solve_s; the rest goes through REF_COLUMNS."""
    sweep = tmp_path / "eth_sep2026_results.csv"
    pd.DataFrame({"cat": ["GHSL_1", "GHSL_9"], "npc": [1000.0, 5.0], "lcoe": [2.0, 1.0],
                  "served_energy_disc_kwh": [500.0, 5.0], "cap_solar": [10.0, 1.0],
                  "cap_battery": [40.0, 1.0], "solve_s": [100.0, 1.0],
                  "code_version": ["6031293", "6031293"]}).to_csv(sweep, index=False)
    new = tmp_path / "projects"
    _summary(new, "GHSL_1", npc=1100.0, energy=500.0, bat=44.0, solve=110.0)
    both = compare_runs.compare(compare_runs.collect(new)[0], compare_runs.load_reference(sweep))
    row = both.set_index("cat").loc["GHSL_1"]
    assert row["npc_ratio"] == pytest.approx(1.1)
    assert row["bat_kwh_ratio"] == pytest.approx(1.1)
    assert row["energy_ratio"] == pytest.approx(1.0)
