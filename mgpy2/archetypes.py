"""
archetypes.py — hourly demand profiles from the published archetype release.

Source: Stevanato et al., "Archetypes of Rural Users in Sub-Saharan Africa for Load
Demand Estimation", dataset release v1.0.0, DOI 10.5281/zenodo.22832973, CC-BY-4.0.
The release files live in data/archetypes/v1.0.0/ together with SHA256SUMS.

Why this module exists
  Until October 2026 demand was read from 110 Excel files inside engines/old (through a
  module that imports streamlit). Three of those files (NC_F1 tiers 1-3) held the next
  tier's profile; tier-3 no-cooling households at 10-30 N got 8.5x their demand. The
  other 103 files match the release to 4-decimal rounding (<= 5e-5 W per hour). Reading
  the release directly fixes the three profiles, removes the streamlit dependency from
  input prep, and gives every summary.json a citable version + DOI.

What it reproduces exactly (so unaffected clusters keep their demand)
  * household series are for 100 households: a cluster with n households of a tier gets
    n / 100 x the series (engines/old Household.load_demand);
  * health-facility series are for one facility: n facilities get n x the series;
  * latitude zones use the bands of engines/old determine_zone, where F1 is 10-30 N.
    The release documents F1 as 10-20 N; which limit to use is checklist item D10.

Units: the release gives mean power per hour in W, numerically equal to Wh in that hour.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from mgpy2.paths import archetypes_dir

RELEASE_VERSION = "1.0.0"
RELEASE_DOI = "10.5281/zenodo.22832973"

HOURS_PER_YEAR = 8760
HOUSEHOLDS_PER_SERIES = 100          # each household series aggregates 100 households
COOLING_REGIMES = ("NC", "AY", "OM", "AS")
ZONES = ("F1", "F2", "F3", "F4", "F5")
TIERS = (1, 2, 3, 4, 5)

_FILES = {
    "households": "households_hourly.csv",
    "health": "health_facilities_hourly.csv",
    "school": "school_hourly.csv",
}


class ArchetypeError(ValueError):
    """The release on disk is missing, altered, or asked for something it does not contain."""


@dataclass(frozen=True)
class ArchetypeRelease:
    """One loaded release. Arrays are read-only so no caller can change the shared copy."""
    version: str
    doi: str
    households: dict[str, np.ndarray] = field(repr=False)   # "HH_NC_F1_T1" -> (8760,) W per 100 HH
    health: dict[str, np.ndarray] = field(repr=False)       # "HF_T1" -> (8760,) W per facility
    school: np.ndarray = field(repr=False)                  # (8760,) W per school
    sha256: dict[str, str] = field(default_factory=dict)

    def provenance(self) -> dict:
        """What summary.json records about the demand data."""
        return {"version": self.version, "doi": self.doi, "sha256": dict(self.sha256)}


# ---------------------------------------------------------------------------------------
# Loading and integrity checks
# ---------------------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_checksums(folder: Path) -> dict[str, str]:
    sums = folder / "SHA256SUMS"
    if not sums.is_file():
        raise ArchetypeError(f"{sums} is missing; it pins the release files.")
    out = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            out[name.strip().lstrip("*")] = digest
    return out


def _read_series_table(path: Path, prefix: str) -> dict[str, np.ndarray]:
    """Read one *_hourly.csv into {archetype_id: (8760,) float array}, with checks."""
    df = pd.read_csv(path)
    if len(df) != HOURS_PER_YEAR or not np.array_equal(df["hour_of_year"].to_numpy(),
                                                       np.arange(HOURS_PER_YEAR)):
        raise ArchetypeError(f"{path.name}: expected hour_of_year 0..{HOURS_PER_YEAR - 1} in order.")
    series = {}
    for col in (c for c in df.columns if c.startswith(prefix)):
        values = df[col].to_numpy(dtype="float64")
        if not np.isfinite(values).all() or (values < 0).any():
            raise ArchetypeError(f"{path.name}: column {col} has negative or non-finite values.")
        values.setflags(write=False)
        series[col] = values
    return series


@lru_cache(maxsize=4)
def load_release(folder: Optional[Path] = None) -> ArchetypeRelease:
    """Load (once per process) and verify the release.

    Every file is checked against SHA256SUMS before use: if someone edits a CSV, or a copy
    drifts from the published release, input prep stops instead of silently using it.
    """
    folder = Path(folder) if folder is not None else archetypes_dir(RELEASE_VERSION)
    expected = _read_checksums(folder)
    sha = {}
    for name in _FILES.values():
        path = folder / name
        if not path.is_file():
            raise ArchetypeError(f"{path} is missing.")
        digest = _sha256(path)
        if expected.get(name) != digest:
            raise ArchetypeError(f"{path.name} does not match SHA256SUMS (got {digest[:12]}...); "
                                 "the file differs from the published release.")
        sha[name] = digest

    households = _read_series_table(folder / _FILES["households"], "HH_")
    health = _read_series_table(folder / _FILES["health"], "HF_")
    school = _read_series_table(folder / _FILES["school"], "SCH_")

    missing = [household_series_id(c, z, t) for c in COOLING_REGIMES for z in ZONES for t in TIERS
               if household_series_id(c, z, t) not in households]
    missing += [f"HF_T{t}" for t in TIERS if f"HF_T{t}" not in health]
    if missing or "SCH_T1" not in school:
        raise ArchetypeError(f"release is incomplete; missing series: {missing or ['SCH_T1']}")

    return ArchetypeRelease(version=RELEASE_VERSION, doi=RELEASE_DOI, households=households,
                            health=health, school=school["SCH_T1"], sha256=sha)


# ---------------------------------------------------------------------------------------
# Selecting and scaling series
# ---------------------------------------------------------------------------------------
def household_series_id(cooling: str, zone: str, tier: int) -> str:
    return f"HH_{cooling}_{zone}_T{tier}"


def zone_for_latitude(lat: float) -> str:
    """Latitude band of the archetypes, identical to engines/old determine_zone.

    F1 is 10-30 N here (the release says 10-20 N; see checklist D10 before changing it).
    """
    if lat is None or not np.isfinite(lat):
        raise ArchetypeError("latitude is missing or not a number.")
    if 10 <= lat <= 30:
        return "F1"
    if -10 <= lat < 10:
        return "F2"
    if -20 <= lat < -10:
        return "F3"
    if -30 <= lat < -20:
        return "F4"
    if lat < -30:
        return "F5"
    raise ArchetypeError(f"latitude {lat} is outside the archetypes' range (up to 30 N).")


def household_load_wh(release: ArchetypeRelease, *, lat: float, cooling: str,
                      households_by_tier: Sequence[float]) -> np.ndarray:
    """Hourly household demand (Wh) of one cluster for one year.

    households_by_tier: number of households in tiers 1..5 (the sample's h_tier1..h_tier5).
    Tiers are added in order 1..5, as engines/old did, so sums match to the last bit when
    the profiles are equal.
    """
    if cooling not in COOLING_REGIMES:
        raise ArchetypeError(f"unknown cooling regime {cooling!r}; expected one of {COOLING_REGIMES}.")
    if len(households_by_tier) != len(TIERS):
        raise ArchetypeError("households_by_tier needs one count per tier (5 values).")
    zone = zone_for_latitude(lat)
    total = np.zeros(HOURS_PER_YEAR)
    for tier, n in zip(TIERS, households_by_tier):
        if n > 0:
            total += n / HOUSEHOLDS_PER_SERIES * release.households[household_series_id(cooling, zone, tier)]
    return total


def health_load_wh(release: ArchetypeRelease, *, facilities_by_tier: Sequence[float]) -> np.ndarray:
    """Hourly health-facility demand (Wh) for one year; one series = one facility.

    facilities_by_tier: counts for HF_T1..HF_T5, i.e. the sample's hospital_1..hospital_5.
    How the sample's health fields map onto these tiers is checklist item I02.
    """
    if len(facilities_by_tier) != len(TIERS):
        raise ArchetypeError("facilities_by_tier needs one count per tier (5 values).")
    total = np.zeros(HOURS_PER_YEAR)
    for tier, n in zip(TIERS, facilities_by_tier):
        if n > 0:
            total += n * release.health[f"HF_T{tier}"]
    return total


def school_shape(release: ArchetypeRelease) -> np.ndarray:
    """School hourly profile normalised to sum 1 (identical to data_sheet/School_weights.csv
    within 5e-10). Multiplied by the sample's school_total_demand (Wh/yr) in input prep."""
    return release.school / release.school.sum()
