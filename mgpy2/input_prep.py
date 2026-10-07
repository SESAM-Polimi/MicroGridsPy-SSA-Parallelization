"""
input_prep.py — produce NEW-engine input CSVs from the OLD engine's science.

Three deliverables per cluster, written into `<project>/inputs/`:

  load_demand.csv            2-row header (scenario, year); col ("meta","hour")=0..8759;
                             values = hourly demand in **kWh**.
  resource_availability.csv  3-row header (scenario, year, resource); col ("meta","hour","");
                             values = **capacity factor** (0..1) per renewable resource.
  ambient_temperature.csv    same 2-row layout as load_demand.csv; values = hourly
                             ambient temperature in **degC** (PVGIS TMY T2m, the same
                             download as the solar series; typical year repeated).

Demand follows the thesis pipeline (old run_nostreamlit_update.run_yaml), with profiles
from the archetype release v1.0.0 (mgpy2.archetypes, DOI 10.5281/zenodo.22832973) instead
of the Excel copies in engines/old (three of which were wrong until Oct 2026):
  * household + hospital demand: n/100 x household series, n x health-facility series;
  * school demand: the release's school profile (normalised; equal to the former
    Data_sheet/School_weights.csv within 5e-10) x the per-row `school_total_demand`,
    grown year-on-year at `demand_growth`;
  * solar via microgridspy.utils.pvgis.download_pvgis_pv_data (its T2m series is also
    returned, `return_temperature=True`, so temperature costs no extra request).

Unit bridges (the silent-risk conversions):
  * old demand is in **Wh**  -> divide by 1000 for kWh;
  * old PV series is energy for a `nom_power`-W array -> divide by nom_power for the
    dimensionless capacity factor the new model multiplies by installed kW.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from mgpy2.archetypes import TIERS, health_load_wh, household_load_wh, load_release, school_shape
from mgpy2.paths import ensure_engines_importable

PERIODS_PER_YEAR = 8760


# =============================================================================
# Config carried from the golden template / sample row
# =============================================================================
@dataclass
class ResourceParams:
    """PVGIS inputs (mirror old a.yaml resource_assessment)."""
    lat: float
    lon: float
    nom_power: float = 1000.0
    tilt: float = 10.0
    azim: float = 180.0
    ro_ground: float = 0.2
    k_T: float = -0.37
    NMOT: float = 45.0
    T_NMOT: float = 20.0
    G_NMOT: float = 800.0
    base_url: str = "https://re.jrc.ec.europa.eu/api/tmy?"
    output_format: str = "json"


@dataclass
class PrepConfig:
    years: int
    year_labels: Sequence[str]
    demand_growth: float
    scenario: str = "scenario_1"
    resource_labels: Sequence[str] = field(default_factory=lambda: ["Solar"])
    periods: int = PERIODS_PER_YEAR
    csv_sep: str = ","
    csv_decimal: str = "."
    # Demand-growth semantics (see module note below):
    #   "thesis_faithful" — reproduce the ORIGINAL thesis pipeline EXACTLY, quirks
    #       included: households/hospitals grow at demand_growth/100 per year (the old
    #       apply_demand_growth divides by 100, so YAML 0.03 => 0.03%/yr), while school
    #       load grows at (1+demand_growth)**y (=> 3%/yr for 0.03). Y1 reproduces the
    #       thesis demand_year1_kwh exactly.
    #   "consistent" — apply ONE uniform compound growth (1+demand_growth)**y to the
    #       whole demand (households, hospitals AND schools). Use if you want to correct
    #       the original inconsistency; results will differ from the thesis after Y1.
    demand_growth_mode: str = "thesis_faithful"


def year_labels(start_year: int, years: int) -> List[str]:
    return [str(start_year + i) for i in range(years)]


# =============================================================================
# Demand (kWh) — shape (periods, years)
# =============================================================================
def compute_demand_kwh(row: dict, cfg: PrepConfig,
                       weights_path: Optional[Path] = None) -> np.ndarray:
    """Return an (periods, years) array of hourly demand in kWh.

    Profiles come from the archetype release (mgpy2.archetypes). `weights_path` is a
    legacy override for the school profile; by default the release's school series is used.
    """
    periods, years = cfg.periods, cfg.years
    if periods != PERIODS_PER_YEAR:
        raise ValueError(f"archetype profiles are hourly ({PERIODS_PER_YEAR} periods), got {periods}.")
    release = load_release()
    growth = cfg.demand_growth

    def _num(key, default=0):
        v = row.get(key, default)
        try:
            return float(v) if v not in (None, "", "nan", "NaN") else float(default)
        except (TypeError, ValueError):
            return float(default)

    cooling = row.get("cooling", "NC")
    if pd.isna(cooling) or str(cooling).strip() in ("", "nan", "NaN", "NA", "None"):
        cooling = "NC"

    faithful = (cfg.demand_growth_mode == "thesis_faithful")
    # In faithful mode the old apply_demand_growth (÷100) grows households/hospitals
    # internally; in consistent mode we suppress internal growth and grow uniformly.
    internal_growth = growth if faithful else 0.0

    # --- household + hospital load (Wh), year 1; schools handled below ---
    year1_wh = (
        household_load_wh(release, lat=_num("lat"), cooling=str(cooling),
                          households_by_tier=[_num(f"h_tier{t}") for t in TIERS])
        + health_load_wh(release, facilities_by_tier=[_num(f"hospital_{t}") for t in TIERS])
    )
    # Years 2..N. Faithful mode reproduces engines/old apply_demand_growth exactly: it divides
    # the rate by 100 (YAML 0.03 => 0.03 %/yr) and compounds column by column. Consistent mode
    # passes internal_growth = 0 here and grows the whole demand uniformly further down.
    load_wh = np.empty((periods, years), dtype="float64")
    load_wh[:, 0] = year1_wh
    for y in range(1, years):
        load_wh[:, y] = load_wh[:, y - 1] * (1 + internal_growth / 100)

    # --- school demand (Wh) distributed by weights profile ---
    school_total = _num("school_total_demand", 0.0)
    if school_total != 0.0:
        if weights_path:   # legacy override, e.g. to reproduce a run made with School_weights.csv
            weights = pd.to_numeric(pd.read_csv(weights_path)["weights"], errors="coerce").to_numpy()
        else:
            weights = school_shape(release)
        if weights.shape[0] != periods:
            raise ValueError(f"school profile has {weights.shape[0]} rows, expected {periods}.")
        profile = weights * school_total  # per-hour Wh, sums to school_total in year 1
        school_wh = np.empty((periods, years), dtype="float64")
        for y in range(years):
            # faithful: school grows at (1+g)^y (thesis behaviour, even though households
            # effectively used g/100). consistent: same (1+g)^y applied to everything below.
            factor = ((1.0 + growth) ** y) if faithful else 1.0
            school_wh[:, y] = profile * factor
        load_wh = load_wh + school_wh

    # --- consistent mode: one uniform compound growth over the whole demand ---
    if not faithful:
        base = load_wh[:, 0].copy()
        for y in range(years):
            load_wh[:, y] = base * ((1.0 + growth) ** y)

    demand_kwh = load_wh / 1000.0
    if not np.isfinite(demand_kwh).all():
        raise ValueError("Computed demand contains non-finite values.")
    if demand_kwh.sum() <= 0:
        raise ValueError("Total demand is zero (no household, hospital, or school load).")
    return demand_kwh


# =============================================================================
# Resource capacity factor (0..1) — shape (periods, years)
# =============================================================================
PVGIS_ATTEMPTS = 3          # tries per cluster before giving up
PVGIS_RETRY_WAIT_S = 30     # wait 30 s, then 60 s, ... between tries


def _download_pv_with_retry(res: ResourceParams):
    """Call the PVGIS download, retrying transient failures. Returns (pv, T2m degC list).

    Only network/HTTP/response problems are retried: requests errors, the ValueError the
    downloader raises on a non-200 status, and a KeyError for an unexpected JSON body.
    Anything else is a bug and is raised immediately.
    """
    import requests
    from microgridspy.utils.pvgis import download_pvgis_pv_data  # old engine

    last_error: Optional[Exception] = None
    for attempt in range(1, PVGIS_ATTEMPTS + 1):
        try:
            return download_pvgis_pv_data(
                res_name="Solar PV", base_URL=res.base_url, output_format=res.output_format,
                lat=res.lat, lon=res.lon, nom_power=res.nom_power, tilt=res.tilt, azimuth=res.azim,
                ro_ground=res.ro_ground, k_T=res.k_T, NMOT=res.NMOT, T_NMOT=res.T_NMOT, G_NMOT=res.G_NMOT,
                return_temperature=True,
            )
        except (requests.RequestException, ValueError, KeyError) as e:
            last_error = e
            print(f"[input_prep][WARN] PVGIS attempt {attempt}/{PVGIS_ATTEMPTS} failed "
                  f"for lat={res.lat} lon={res.lon}: {e}")
            if attempt < PVGIS_ATTEMPTS:
                time.sleep(PVGIS_RETRY_WAIT_S * attempt)
    raise RuntimeError(
        f"PVGIS download failed after {PVGIS_ATTEMPTS} attempts "
        f"(lat={res.lat}, lon={res.lon}): {last_error}"
    ) from last_error


AMBIENT_TEMP_MIN_C = -60.0
AMBIENT_TEMP_MAX_C = 70.0


def compute_resource_and_temperature(res: ResourceParams, cfg: PrepConfig):
    """Return (cf, temperature_degC), each (periods, years). Typical year repeated across years.

    One PVGIS request feeds both: the solar capacity factor and the ambient (T2m)
    temperature series, so they are consistent by construction.

    Fails loudly: if PVGIS cannot be reached (after retries) or returns no sun at all,
    an exception is raised, so the cluster ends with error.txt and appears on the
    rerun list. (Until Sep 2026 a failure silently became ZERO sun.)
    """
    ensure_engines_importable()
    periods, years = cfg.periods, cfg.years

    pv, t_amb = _download_pv_with_retry(res)
    if isinstance(pv, pd.DataFrame):
        series = pv.iloc[:, 0].to_numpy(dtype="float64")
    else:
        series = np.asarray(pv, dtype="float64").ravel()
    cf_year = series / float(res.nom_power)  # energy-for-nom_power -> per-unit CF

    if cf_year.shape[0] != periods:
        raise ValueError(f"PVGIS returned {cf_year.shape[0]} periods, expected {periods}.")
    cf_year = np.clip(cf_year, 0.0, None)  # guard tiny negatives from temp term
    if not np.isfinite(cf_year).all() or cf_year.max() <= 0.0:
        raise ValueError(f"PVGIS data for lat={res.lat} lon={res.lon} has no usable solar output.")

    temp_year = np.asarray(t_amb, dtype="float64").ravel()
    if temp_year.shape[0] != periods:
        raise ValueError(f"PVGIS returned {temp_year.shape[0]} temperature periods, expected {periods}.")
    if (not np.isfinite(temp_year).all()
            or temp_year.min() < AMBIENT_TEMP_MIN_C or temp_year.max() > AMBIENT_TEMP_MAX_C):
        raise ValueError(f"PVGIS temperature for lat={res.lat} lon={res.lon} is non-finite or outside "
                         f"[{AMBIENT_TEMP_MIN_C}, {AMBIENT_TEMP_MAX_C}] degC.")
    return (np.tile(cf_year.reshape(periods, 1), (1, years)),
            np.tile(temp_year.reshape(periods, 1), (1, years)))


# =============================================================================
# Writers (match the engine template layout exactly, then write via engine I/O)
# =============================================================================
def _hour_col_index(periods: int) -> np.ndarray:
    return np.arange(periods, dtype="int64")


def build_load_demand_df(demand_kwh: np.ndarray, cfg: PrepConfig) -> pd.DataFrame:
    periods, years = demand_kwh.shape
    cols = [("meta", "hour")]
    for y in cfg.year_labels:
        cols.append((str(cfg.scenario), str(y)))
    columns = pd.MultiIndex.from_tuples(cols, names=["scenario", "year"])
    df = pd.DataFrame(index=range(periods), columns=columns, dtype="float64")
    df[("meta", "hour")] = _hour_col_index(periods)
    for j, y in enumerate(cfg.year_labels):
        df[(str(cfg.scenario), str(y))] = demand_kwh[:, j]
    return df


def build_ambient_temperature_df(temp_degc: np.ndarray, cfg: PrepConfig) -> pd.DataFrame:
    """ambient_temperature.csv shares the load_demand.csv layout (values in degC)."""
    return build_load_demand_df(temp_degc, cfg)


def build_resource_df(cf: np.ndarray, cfg: PrepConfig) -> pd.DataFrame:
    periods, years = cf.shape
    cols = [("meta", "hour", "")]
    for y in cfg.year_labels:
        for r in cfg.resource_labels:
            cols.append((str(cfg.scenario), str(y), str(r)))
    columns = pd.MultiIndex.from_tuples(cols, names=["scenario", "year", "resource"])
    df = pd.DataFrame(index=range(periods), columns=columns, dtype="float64")
    df[("meta", "hour", "")] = _hour_col_index(periods)
    # same typical-year CF for every resource column (single-resource default: Solar)
    for y in cfg.year_labels:
        for j, r in enumerate(cfg.resource_labels):
            df[(str(cfg.scenario), str(y), str(r))] = cf[:, cfg.year_labels.index(y)]
    return df


def _write_csv(df: pd.DataFrame, path: Path, cfg: PrepConfig) -> None:
    ensure_engines_importable()
    from core.io.csv_format import write_csv_with_format  # new engine
    path.parent.mkdir(parents=True, exist_ok=True)
    write_csv_with_format(df, path, csv_format={"sep": cfg.csv_sep, "decimal": cfg.csv_decimal}, index=False)


def write_demand_and_resource(inputs_dir: Path, row: dict, res: ResourceParams,
                              cfg: PrepConfig, weights_path: Optional[Path] = None) -> None:
    """High-level: compute + write demand, resource and ambient-temperature CSVs into inputs_dir."""
    inputs_dir = Path(inputs_dir)
    demand_kwh = compute_demand_kwh(row, cfg, weights_path=weights_path)
    cf, temp_degc = compute_resource_and_temperature(res, cfg)
    _write_csv(build_load_demand_df(demand_kwh, cfg), inputs_dir / "load_demand.csv", cfg)
    _write_csv(build_resource_df(cf, cfg), inputs_dir / "resource_availability.csv", cfg)
    _write_csv(build_ambient_temperature_df(temp_degc, cfg), inputs_dir / "ambient_temperature.csv", cfg)


# =============================================================================
# Self-test: format round-trip through the engine's own reader (no network)
# =============================================================================
def _selftest() -> int:
    import tempfile
    from core.io.csv_format import read_csv_with_format  # new engine

    years = 3
    cfg = PrepConfig(years=years, year_labels=year_labels(2025, years),
                     demand_growth=0.03, resource_labels=["Solar"])
    rng = np.random.default_rng(0)
    demand = rng.random((PERIODS_PER_YEAR, years)) * 10.0
    cf = np.clip(rng.random((PERIODS_PER_YEAR, years)), 0, 1)
    temp = 15.0 + 10.0 * rng.random((PERIODS_PER_YEAR, 1)).repeat(years, axis=1)

    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        _write_csv(build_load_demand_df(demand, cfg), dd / "load_demand.csv", cfg)
        _write_csv(build_resource_df(cf, cfg), dd / "resource_availability.csv", cfg)
        _write_csv(build_ambient_temperature_df(temp, cfg), dd / "ambient_temperature.csv", cfg)

        ld = read_csv_with_format(dd / "load_demand.csv", header=[0, 1])
        ra = read_csv_with_format(dd / "resource_availability.csv", header=[0, 1, 2])
        assert ld.shape[0] == PERIODS_PER_YEAR, ld.shape
        assert ra.shape[0] == PERIODS_PER_YEAR, ra.shape
        # hour column round-trips
        assert int(ld.iloc[0, 0]) == 0 and int(ld.iloc[-1, 0]) == PERIODS_PER_YEAR - 1
        # value round-trips (first scenario/year demand column)
        assert np.allclose(ld.iloc[:, 1].to_numpy(dtype=float), demand[:, 0], atol=1e-6)
        assert np.allclose(ra.iloc[:, 1].to_numpy(dtype=float), cf[:, 0], atol=1e-6)
        at = read_csv_with_format(dd / "ambient_temperature.csv", header=[0, 1])
        assert at.shape[0] == PERIODS_PER_YEAR, at.shape
        assert np.allclose(at.iloc[:, 1].to_numpy(dtype=float), temp[:, 0], atol=1e-6)
    print("[input_prep selftest] OK — CSV format round-trips through engine reader.")
    return 0


if __name__ == "__main__":
    import sys
    from mgpy2.paths import ensure_engines_importable
    ensure_engines_importable()
    sys.exit(_selftest())
