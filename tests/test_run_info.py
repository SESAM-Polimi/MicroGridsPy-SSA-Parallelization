"""summary.json provenance: the per-cluster battery-ageing values reach meta.run.

Run from the repo root:  python -m pytest tests/test_run_info.py -q
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np

from mgpy2.config_map import ThesisConfig
from mgpy2.run_cluster import BATTERY_DEGRADATION_FIELDS, _battery_degradation_info, _run_info

DERIVED = {   # shape of settings.battery_model.degradation_model after a derived-life run
    "coefficients_enabled": True,
    "chemistry": "LFP",
    "calendar_lifetime_mode": "derived",
    "calendar_lifetime_years_derived": np.float64(7.0253842272),
    "calendar_lifetime_years_used": np.float64(7.0),
    "mean_cell_temperature_c": 38.35,
    "assumed_equivalent_full_cycles_per_year": 231.27,
    "assumed_cycle_fade_per_year": 0.01339,
    "discharge_weighted_cycle_fade_coefficient": 7.24e-05,
    "calendar_fade_budget_fraction": 0.453,
    "cycle_lifetime_to_eol_cycles": 6000.0,
    "end_of_life_soh": 0.8,
    "cycle_life_scaling": 1.2287,
    "battery_calendar_fade_curve_csv": None,      # engine internals: not exported
}


def _data(degradation_model):
    return SimpleNamespace(attrs={"settings": {"battery_model": {"degradation_model": degradation_model}}})


def test_derived_life_is_exported_as_plain_json():
    info = _battery_degradation_info(_data(DERIVED))
    assert info["calendar_lifetime_years_used"] == 7.0
    assert info["mean_cell_temperature_c"] == 38.35
    assert set(info) <= set(BATTERY_DEGRADATION_FIELDS)
    assert "battery_calendar_fade_curve_csv" not in info
    json.dumps(info)                               # numpy scalars were converted


def test_missing_fields_are_left_out():
    info = _battery_degradation_info(_data({"coefficients_enabled": False}))
    assert info == {"coefficients_enabled": False}


def test_no_data_gives_empty_block():
    assert _battery_degradation_info(None) == {}


def test_settings_serialised_as_string_are_read():
    data = SimpleNamespace(attrs={"settings": json.dumps(
        {"battery_model": {"degradation_model": {"calendar_lifetime_years_used": 9.0}}})})
    assert _battery_degradation_info(data) == {"calendar_lifetime_years_used": 9.0}


def test_run_info_carries_the_block():
    info = _run_info(ThesisConfig(), "highs", {}, "optimal", 1.0, 2.0, data=_data(DERIVED))
    assert info["battery_degradation"]["calendar_lifetime_years_used"] == 7.0
    json.dumps(info, default=str)


# ---- code_version fallback without the git program ---------------------------------------
from mgpy2.run_cluster import _head_commit_from_files


def _fake_repo(tmp_path, head, loose=None, packed=None):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head)
    if loose:
        (git / "refs" / "heads" / "main").write_text(loose)
    if packed:
        (git / "packed-refs").write_text(packed)
    return tmp_path


def test_head_from_loose_ref(tmp_path):
    repo = _fake_repo(tmp_path, "ref: refs/heads/main\n", loose="f2f8cc4d1e9a0b7c\n")
    assert _head_commit_from_files(repo) == "f2f8cc4"


def test_head_from_packed_refs(tmp_path):
    packed = ("# pack-refs with: peeled fully-peeled sorted\n"
              "1111111aaaaaaa refs/heads/other\n"
              "f2f8cc4d1e9a0b7c refs/heads/main\n"
              "^2222222bbbbbbb\n")
    repo = _fake_repo(tmp_path, "ref: refs/heads/main\n", packed=packed)
    assert _head_commit_from_files(repo) == "f2f8cc4"


def test_detached_head_and_missing_repo(tmp_path):
    assert _head_commit_from_files(_fake_repo(tmp_path, "f2f8cc4d1e9a0b7c\n")) == "f2f8cc4"
    assert _head_commit_from_files(tmp_path / "nowhere") is None
