"""
postprocess.py — aggregate NEW-engine per-cluster results back into the sample table.

Replaces the old postprocess.py which read costs.csv / sizing_results.csv. The new
engine writes a results bundle per cluster; this module reads:

  reporting_summary.csv  -> LCOE, Net Present Cost, investment cost (present/nominal)
  capacity_by_year.csv   -> total installed PV (kW) and Battery (kWh) [last year]
  design_by_step.csv      -> fallback sizing (sum of steps)

Outputs an enriched copy of the sample CSV and (if geopandas is available) a
GeoPackage point map, mirroring the old behaviour.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from mgpy2.paths import projects_root


def _results_dir(cat: str, projects: Path) -> Path:
    return projects / str(cat) / "results"


def read_reporting_summary(results_dir: Path) -> Dict[str, Optional[float]]:
    out = {"lcoe": None, "npc": None, "investment_present": None, "investment_nominal": None}
    f = results_dir / "reporting_summary.csv"
    if not f.exists():
        return out
    try:
        df = pd.read_csv(f)
    except Exception:
        return out

    def _val(label_contains: str) -> Optional[float]:
        m = df[(df["section"].astype(str) == "summary_metrics")
               & (df["row_label"].astype(str).str.contains(label_contains, case=False, na=False))]
        if m.empty:
            return None
        return pd.to_numeric(m["value"], errors="coerce").dropna().iloc[0] if not m.empty else None

    out["lcoe"] = _val("LCOE")
    out["npc"] = _val("Net Present Cost")
    out["investment_present"] = _val("Investment cost (present)")
    out["investment_nominal"] = _val("Investment cost (nominal)")
    return out


def read_sizing(results_dir: Path) -> Dict[str, Optional[float]]:
    out = {"pv_kw": None, "battery_kwh": None, "generator_kw": None}
    cby = results_dir / "capacity_by_year.csv"
    if cby.exists():
        try:
            df = pd.read_csv(cby)
            if not df.empty:
                last = df.sort_values("year").iloc[-1]
                out["pv_kw"] = float(last.get("renewables_kw", float("nan")))
                out["battery_kwh"] = float(last.get("battery_kwh", float("nan")))
                out["generator_kw"] = float(last.get("generator_kw", float("nan")))
                return out
        except Exception:
            pass
    # fallback: sum installed capacity across steps
    dbs = results_dir / "design_by_step.csv"
    if dbs.exists():
        try:
            df = pd.read_csv(dbs)
            def _sum(tech):
                s = df[df["technology"].astype(str) == tech]["installed_capacity"]
                return float(pd.to_numeric(s, errors="coerce").sum()) if not s.empty else None
            out["pv_kw"] = _sum("renewable")
            out["battery_kwh"] = _sum("battery")
            out["generator_kw"] = _sum("generator")
        except Exception:
            pass
    return out


def demand_year1_kwh(cat: str, projects: Path) -> Optional[float]:
    """Sum the first-year column of the generated load_demand.csv (already kWh)."""
    f = projects / str(cat) / "inputs" / "load_demand.csv"
    if not f.exists():
        return None
    try:
        df = pd.read_csv(f, header=[0, 1])
        # first data column after ("meta","hour")
        return float(pd.to_numeric(df.iloc[:, 1], errors="coerce").sum())
    except Exception:
        return None


def aggregate(sample_csv: Path, projects: Optional[Path] = None,
              out_csv: Optional[Path] = None, gpkg: Optional[Path] = None) -> pd.DataFrame:
    projects = projects or projects_root()
    df = pd.read_csv(sample_csv)
    df = df[~df.iloc[:, 0].astype(str).str.startswith("//")].reset_index(drop=True)

    for idx, row in df.iterrows():
        cat = row["cat"]
        rd = _results_dir(cat, projects)
        rep = read_reporting_summary(rd)
        siz = read_sizing(rd)
        df.at[idx, "pv (kW)"] = siz["pv_kw"]
        df.at[idx, "battery (kWh)"] = siz["battery_kwh"]
        df.at[idx, "generator (kW)"] = siz["generator_kw"]
        df.at[idx, "lcoe (€/kWh)"] = rep["lcoe"]
        df.at[idx, "npc (€)"] = rep["npc"]
        df.at[idx, "investment_present (€)"] = rep["investment_present"]
        df.at[idx, "investment_nominal (€)"] = rep["investment_nominal"]
        df.at[idx, "demand_year1_kwh"] = demand_year1_kwh(cat, projects)

    if out_csv:
        df.to_csv(out_csv, index=False)

    if gpkg and {"lat", "lon"}.issubset(df.columns):
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            geometry = [Point(lo, la) for lo, la in zip(df["lon"], df["lat"])]
            gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
            gdf.to_file(gpkg, driver="GPKG")
        except Exception as e:
            print(f"[postprocess] GeoPackage export skipped: {e}")

    return df


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default=str((projects_root().parent / "advanced_sample.csv")))
    ap.add_argument("--projects", default=str(projects_root()))
    ap.add_argument("--out", default=None, help="output CSV (default: <sample>_results.csv)")
    ap.add_argument("--gpkg", default=None)
    args = ap.parse_args()
    sample = Path(args.sample)
    out = Path(args.out) if args.out else sample.with_name(sample.stem + "_results.csv")
    df = aggregate(sample, Path(args.projects), out_csv=out,
                   gpkg=Path(args.gpkg) if args.gpkg else None)
    done = int(df["lcoe (€/kWh)"].notna().sum())
    print(f"[postprocess] {done}/{len(df)} clusters with results -> {out}")
