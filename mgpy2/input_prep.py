"""
input_prep.py — produce NEW-engine input CSVs from the OLD engine's science.

Two deliverables per cluster, written into `<project>/inputs/`:

  load_demand.csv            2-row header (scenario, year); col ("meta","hour")=0..8759;
                             values = hourly demand in **kWh**.
  resource_availability.csv  3-row header (scenario, year, resource); col ("meta","hour","");
                             values = **capacity factor** (0..1) per renewable resource.

Faithful to the thesis pipeline (old run_nostreamlit_update.run_yaml):
  * household + hospital demand via microgridspy.utils.archetypes.demand_calculation
    (called with num_schools=0);
  * school demand distributed separately via Data_sheet/School_weights.csv and the
    per-row `school_total_demand`, grown year-on-year at `demand_growth`;
  * solar via microgridspy.utils.pvgis.download_pvgis_pv_data.

Unit bridges (the silent-risk conversions):
  * old demand is in **Wh**  -> divide by 1000 for kWh;
  * old PV series is energy for a `nom_power`-W array -> divide by nom_power for the
    dimensionless capacity factor the new model multiplies by installed kW.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from mgpy2.paths import ensure_engines_importable, school_weights_csv

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
    """Return an (periods, years) array of hourly demand in kWh."""
    ensure_engines_importable()
    from microgridspy.utils.archetypes import demand_calculation  # old engine

    periods, years = cfg.periods, cfg.years
    growth = cfg.demand_growth

    def _num(key, default=0):
        v = row.get(key, default)
        try:
            return float(v) if v not in (None, "", "nan", "NaN") else float(default)
        except (TypeError, ValueError):
            return float(default)

    cooling = row.get("cooling", "NC")
    if cooling in (None, "", "nan", "NaN", "NA"):
        cooling = "NC"

    faithful = (cfg.demand_growth_mode == "thesis_faithful")
    # In faithful mode the old apply_demand_growth (÷100) grows households/hospitals
    # internally; in consistent mode we suppress internal growth and grow uniformly.
    internal_growth = growth if faithful else 0.0

    # --- household + hospital load (Wh); schools handled below ---
    try:
        load_total, _users = demand_calculation(
            _num("lat"), str(cooling),
            _num("h_tier1"), _num("h_tier2"), _num("h_tier3"), _num("h_tier4"), _num("h_tier5"),
            0,  # schools handled via weights, exactly like the thesis pipeline
            _num("hospital_1"), _num("hospital_2"), _num("hospital_3"), _num("hospital_4"), _num("hospital_5"),
            internal_growth, years, periods,
        )
    except ValueError as e:
        if "Total load is zero" in str(e):
            load_total = pd.DataFrame(
                0.0, index=range(periods), columns=[f"Year_{i+1}" for i in range(years)]
            )
        else:
            raise

    load_wh = load_total.to_numpy(dtype="float64")  # (periods, years)
    if load_wh.shape != (periods, years):
        raise ValueError(f"demand_calculation returned {load_wh.shape}, expected {(periods, years)}")

    # --- school demand (Wh) distributed by weights profile ---
    school_total = _num("school_total_demand", 0.0)
    if school_total != 0.0:
        wpath = Path(weights_path) if weights_path else school_weights_csv()
        weights = pd.to_numeric(pd.read_csv(wpath)["weights"], errors="coerce").to_numpy()
        if weights.shape[0] != periods:
            raise ValueError(
                f"School_weights.csv has {weights.shape[0]} rows, expected {periods}."
            )
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
def compute_resource_cf(res: ResourceParams, cfg: PrepConfig,
                        allow_zero_fallback: bool = True) -> np.ndarray:
    """Return (periods, years) capacity factors. Typical year repeated across years."""
    ensure_engines_importable()
    from microgridspy.utils.pvgis import download_pvgis_pv_data  # old engine

    periods, years = cfg.periods, cfg.years
    try:
        pv = download_pvgis_pv_data(
            res_name="Solar PV", base_URL=res.base_url, output_format=res.output_format,
            lat=res.lat, lon=res.lon, nom_power=res.nom_power, tilt=res.tilt, azimuth=res.azim,
            ro_ground=res.ro_ground, k_T=res.k_T, NMOT=res.NMOT, T_NMOT=res.T_NMOT, G_NMOT=res.G_NMOT,
        )
        if isinstance(pv, pd.DataFrame):
            series = pv.iloc[:, 0].to_numpy(dtype="float64")
        else:
            series = np.asarray(pv, dtype="float64").ravel()
        cf_year = series / float(res.nom_power)  # energy-for-nom_power -> per-unit CF
    except Exception as e:
        if not allow_zero_fallback:
            raise
        print(f"[input_prep][WARN] PVGIS failed for lat={res.lat} lon={res.lon}: {e}. "
              f"Falling back to zero solar.")
        cf_year = np.zeros(periods, dtype="float64")

    if cf_year.shape[0] != periods:
        raise ValueError(f"PVGIS returned {cf_year.shape[0]} periods, expected {periods}.")
    cf_year = np.clip(cf_year, 0.0, None)  # guard tiny negatives from temp term
    return np.tile(cf_year.reshape(periods, 1), (1, years))


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
    """High-level: compute + write both CSVs into inputs_dir."""
    inputs_dir = Path(inputs_dir)
    demand_kwh = compute_demand_kwh(row, cfg, weights_path=weights_path)
    cf = compute_resource_cf(res, cfg)
    _write_csv(build_load_demand_df(demand_kwh, cfg), inputs_dir / "load_demand.csv", cfg)
    _write_csv(build_resource_df(cf, cfg), inputs_dir / "resource_availability.csv", cfg)


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

    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        _write_csv(build_load_demand_df(demand, cfg), dd / "load_demand.csv", cfg)
        _write_csv(build_resource_df(cf, cfg), dd / "resource_availability.csv", cfg)

        ld = read_csv_with_format(dd / "load_demand.csv", header=[0, 1])
        ra = read_csv_with_format(dd / "resource_availability.csv", header=[0, 1, 2])
        assert ld.shape[0] == PERIODS_PER_YEAR, ld.shape
        assert ra.shape[0] == PERIODS_PER_YEAR, ra.shape
        # hour column round-trips
        assert int(ld.iloc[0, 0]) == 0 and int(ld.iloc[-1, 0]) == PERIODS_PER_YEAR - 1
        # value round-trips (first scenario/year demand column)
        assert np.allclose(ld.iloc[:, 1].to_numpy(dtype=float), demand[:, 0], atol=1e-6)
        assert np.allclose(ra.iloc[:, 1].to_numpy(dtype=float), cf[:, 0], atol=1e-6)
    print("[input_prep selftest] OK — CSV format round-trips through engine reader.")
    return 0


if __name__ == "__main__":
    import sys
    from mgpy2.paths import ensure_engines_importable
    ensure_engines_importable()
    sys.exit(_selftest())
