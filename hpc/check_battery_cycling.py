#!/usr/bin/env python
"""Realised battery cycling in solved clusters vs the cycling the derived battery life assumed.

The derived service life needs a cycling estimate before the LP runs
(meta.run.battery_degradation.assumed_equivalent_full_cycles_per_year in summary.json).
This script measures what the solved dispatch actually did, so the estimate can be checked.

    python hpc/check_battery_cycling.py bench/20261008_cycling/projects --out cycling_check.csv

Needs the dispatch files: run the clusters with DISPATCH_FORMAT=parquet (or csv).

Realised equivalent full cycles in year y, on the same basis as the engine's estimate (the
cycle fade per year is c * n_EFC * DoD as a fraction of nameplate):

    n_y = sum_t (P_dis,t / eta_dis) / (DoD * E_nom,y)

with P_dis the AC discharge, eta_dis the discharge efficiency, DoD the depth of discharge and
E_nom,y the battery nameplate active in year y.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def read_dispatch(results: Path) -> pd.DataFrame | None:
    for name, reader in (("dispatch.parquet", pd.read_parquet), ("dispatch.csv", pd.read_csv)):
        if (results / name).exists():
            return reader(results / name)
    return None


def check_cluster(results: Path) -> dict:
    """One row: assumed vs realised cycles for <projects>/<cat>/results."""
    s = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    run = (s.get("meta") or {}).get("run") or {}
    cfg = run.get("pipeline_config") or {}
    deg = run.get("battery_degradation") or {}
    row = {
        "cat": results.parent.name,
        "cycling_estimate": deg.get("cycling_estimate"),
        "bat_life_y": deg.get("calendar_lifetime_years_used"),
        "assumed_efc": deg.get("assumed_equivalent_full_cycles_per_year"),
    }
    dispatch = read_dispatch(results)
    if dispatch is None:
        row["note"] = "no dispatch file (run with DISPATCH_FORMAT=parquet)"
        return row
    eta_dis = float(cfg.get("battery_discharge_efficiency", 0.9))
    dod = float(cfg.get("battery_depth_of_discharge", 0.8))
    nameplate = {str(r["year"]): float(r["battery_kwh"]) for r in s.get("capacity_by_year") or []}

    dispatch = dispatch.assign(year=dispatch["year"].astype(str))
    discharge = dispatch.groupby("year")["battery_discharge"].sum()   # one scenario in our runs
    if "scenario" in dispatch.columns and dispatch["scenario"].nunique() > 1:
        row["note"] = "several scenarios: discharge summed over them"
    efc = pd.Series({y: (d / eta_dis) / (dod * nameplate[y])
                     for y, d in discharge.items() if nameplate.get(y, 0.0) > 0.0})
    if efc.empty:
        row["note"] = "no battery installed"
        return row
    efc = efc.sort_index()
    row.update({
        "realised_efc_mean": float(efc.mean()),
        "realised_efc_first_year": float(efc.iloc[0]),
        "realised_efc_last_year": float(efc.iloc[-1]),
    })
    if row["assumed_efc"]:
        row["realised_over_assumed"] = row["realised_efc_mean"] / float(row["assumed_efc"])
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("projects", type=Path, help="projects folder of a solved run")
    ap.add_argument("--out", type=Path, help="write the table here (CSV)")
    args = ap.parse_args(argv)
    found = sorted(args.projects.glob("*/results/summary.json"))
    if not found:
        sys.exit(f"no summary.json under {args.projects}/*/results/")
    table = pd.DataFrame([check_cluster(p.parent) for p in found])
    print(table.round(3).to_string(index=False))
    if args.out:
        table.to_csv(args.out, index=False)
        print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
