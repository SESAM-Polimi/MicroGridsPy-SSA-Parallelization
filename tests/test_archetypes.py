"""Tests for mgpy2.archetypes and the demand it feeds into input prep.

Run from the repo root:  python -m pytest tests/test_archetypes.py -q

The two legacy tests compare against the engines/old Excel route that this module replaced.
They skip themselves once engines/old (or streamlit, which it imports) is gone.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mgpy2 import archetypes as A
from mgpy2.input_prep import PrepConfig, compute_demand_kwh, year_labels
from mgpy2.paths import old_engine_dir, repo_root, school_weights_csv

# The three profiles that were wrong in engines/old: each held the next tier's series.
FIXED_IN_RELEASE = {"HH_NC_F1_T1", "HH_NC_F1_T2", "HH_NC_F1_T3"}
EXCEL_DIR = old_engine_dir() / "microgridspy" / "utils" / "demand_archetypes"
ETH_SAMPLE = repo_root() / "data" / "sample_input_2025" / "ETH" / "advanced_sample.csv"


@pytest.fixture(scope="module")
def release() -> A.ArchetypeRelease:
    return A.load_release()


# --- the release itself -------------------------------------------------------------------
def test_release_is_complete(release):
    assert len(release.households) == 100          # 4 cooling regimes x 5 zones x 5 tiers
    assert set(release.health) == {f"HF_T{t}" for t in A.TIERS}
    assert release.school.shape == (A.HOURS_PER_YEAR,)
    assert release.provenance()["doi"] == "10.5281/zenodo.22832973"


def test_series_match_published_annual_energy(release):
    index = pd.read_csv(A.archetypes_dir(A.RELEASE_VERSION) / "archetype_index.csv").set_index("archetype_id")
    for sid, values in {**release.households, **release.health, "SCH_T1": release.school}.items():
        assert values.sum() / 1000 == pytest.approx(index.loc[sid, "annual_energy_kWh"], rel=1e-6), sid


def test_altered_file_is_refused(tmp_path):
    src = A.archetypes_dir(A.RELEASE_VERSION)
    dst = tmp_path / "v1.0.0"
    shutil.copytree(src, dst)
    with open(dst / "school_hourly.csv", "a") as fh:     # one extra byte is enough
        fh.write("\n")
    with pytest.raises(A.ArchetypeError, match="does not match SHA256SUMS"):
        A.load_release(dst)


def test_arrays_are_read_only(release):
    with pytest.raises(ValueError):
        release.households["HH_NC_F1_T1"][0] = 1.0


# --- selection and scaling ----------------------------------------------------------------
@pytest.mark.parametrize("lat, zone", [(30.0, "F1"), (10.0, "F1"), (9.99, "F2"), (-10.0, "F2"),
                                       (-10.01, "F3"), (-20.0, "F3"), (-20.01, "F4"),
                                       (-30.0, "F4"), (-30.01, "F5")])
def test_zone_bands_match_engines_old(lat, zone):
    assert A.zone_for_latitude(lat) == zone


def test_zone_outside_range_is_an_error():
    with pytest.raises(A.ArchetypeError):
        A.zone_for_latitude(31.0)


def test_household_scaling_is_per_100(release):
    one_series = release.households["HH_AY_F2_T3"]
    load = A.household_load_wh(release, lat=0.0, cooling="AY", households_by_tier=[0, 0, 250, 0, 0])
    np.testing.assert_allclose(load, 2.5 * one_series)


def test_unknown_cooling_is_an_error(release):
    with pytest.raises(A.ArchetypeError, match="cooling"):
        A.household_load_wh(release, lat=0.0, cooling="XX", households_by_tier=[1, 0, 0, 0, 0])


def test_school_shape_equals_old_weights(release):
    old = pd.read_csv(school_weights_csv())["weights"].to_numpy()
    np.testing.assert_allclose(A.school_shape(release), old, atol=1e-9)


# --- growth modes in input prep -----------------------------------------------------------
@pytest.mark.parametrize("mode, yearly_factor", [("thesis_faithful", 1.0003), ("consistent", 1.03)])
def test_growth_modes_for_households(mode, yearly_factor):
    row = {"lat": 5.0, "cooling": "AY", "h_tier1": 0, "h_tier2": 100, "h_tier3": 0, "h_tier4": 0,
           "h_tier5": 0, "school_total_demand": 0.0}
    cfg = PrepConfig(years=20, year_labels=year_labels(2025, 20), demand_growth=0.03, demand_growth_mode=mode)
    d = compute_demand_kwh(row, cfg)
    annual = d.sum(axis=0)
    np.testing.assert_allclose(annual / annual[0], yearly_factor ** np.arange(20), rtol=1e-12)


# --- equivalence with the engines/old route this replaced ----------------------------------
def _legacy_excel_series() -> dict[str, np.ndarray]:
    out = {}
    for f in sorted(EXCEL_DIR.glob("*.xlsx")):
        name = f.stem
        if name.startswith("~$"):
            continue
        if name.startswith("HOSPITAL_Tier-"):
            sid = "HF_T" + name.split("-")[1]
        elif name == "SCHOOL":
            sid = "SCH_T1"
        else:
            regime, zone, tier = name.split("_")
            sid = A.household_series_id(regime, zone, int(tier.split("-")[1]))
        out[sid] = pd.read_excel(f, usecols="B").iloc[:, 0].to_numpy(dtype=float)
    return out


@pytest.mark.skipif(not EXCEL_DIR.is_dir(), reason="engines/old archetype files are gone")
def test_release_equals_old_excel_except_the_three_fixed_profiles(release):
    old = _legacy_excel_series()
    new = {**release.households, **release.health, "SCH_T1": release.school}
    assert set(old) == set(new)
    for sid, values in new.items():
        diff = np.abs(values - old[sid]).max()
        if sid in FIXED_IN_RELEASE:
            assert diff > 1000, f"{sid} was expected to change"
        else:
            assert diff <= 5.01e-5, f"{sid} differs from engines/old by {diff} W"   # 4-decimal rounding


@pytest.mark.skipif(not EXCEL_DIR.is_dir() or not ETH_SAMPLE.is_file(), reason="legacy route or sample missing")
def test_year1_demand_unchanged_where_no_fixed_profile_is_used(release):
    pytest.importorskip("streamlit")                 # engines/old archetypes.py imports it
    from mgpy2.paths import ensure_engines_importable
    ensure_engines_importable()
    from microgridspy.utils.archetypes import demand_calculation

    df = pd.read_csv(ETH_SAMPLE, low_memory=False)
    df = df[~df["cat"].astype(str).str.startswith("//")]
    for c in ["lat"] + [f"h_tier{t}" for t in A.TIERS] + [f"hospital_{t}" for t in A.TIERS]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["cooling"] = df["cooling"].where(df["cooling"].isin(A.COOLING_REGIMES), "NC")
    f1 = df["lat"].between(10, 30)
    t13 = df[["h_tier1", "h_tier2", "h_tier3"]].sum(axis=1) > 0
    affected = f1 & (df["cooling"] == "NC") & t13
    hospitals = df[[f"hospital_{t}" for t in A.TIERS]].sum(axis=1) > 0
    picks = pd.concat([df[affected].head(4), df[~affected & f1].head(4), df[~f1 & t13].head(4),
                       df[hospitals & ~affected].head(4)])

    for _, r in picks.iterrows():
        hh = [r[f"h_tier{t}"] for t in A.TIERS]
        hf = [r[f"hospital_{t}"] for t in A.TIERS]
        new = A.household_load_wh(release, lat=r["lat"], cooling=r["cooling"], households_by_tier=hh) \
            + A.health_load_wh(release, facilities_by_tier=hf)
        if sum(hh) + sum(hf) == 0:
            assert not new.any()
            continue
        old, _ = demand_calculation(r["lat"], r["cooling"], *hh, 0, *hf, 0.0, 1, A.HOURS_PER_YEAR)
        old = old.iloc[:, 0].to_numpy()
        if affected[r.name]:
            assert new.sum() < old.sum(), f"{r['cat']}: fixed profiles should lower demand"
        else:
            np.testing.assert_allclose(new, old, rtol=1e-6, atol=1e-3, err_msg=str(r["cat"]))
