#!/usr/bin/env python
"""Compare a folder of new results with a reference table, cluster by cluster.

Typical use (repo root on the cluster, after a test run on the comparison set):

    python hpc/compare_runs.py \
        --new  bench/20261007_battery/projects \
        --ref  compare/20260917/compare_table.csv \
        --out  compare/20261007_battery_vs_sep.csv

What it does
  1. Reads every <new>/<cluster>/results/summary.json (one per solved cluster) and lists
     clusters that have an error.txt instead.
  2. Takes NPC, LCOE, discounted served energy, PV and battery capacity and solve time
     from each summary.
  3. Joins them with the reference table on the cluster id (`cat`) and writes the ratios
     new / reference.
  4. Prints a short report: missing clusters, a demand sanity check, and the ratio ranges
     by cluster type.

The demand check matters: if the discounted served energy differs, the demand changed
between the two runs, and the comparison no longer isolates what you meant to test.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# Reference columns (compare/20260917/compare_table.csv) -> common names used below.
REF_COLUMNS = {
    "sep_objective": "npc",
    "sep_lcoe": "lcoe",
    "served_energy_disc_kwh": "energy",
    "cap_solar": "pv_kw",
    "cap_battery": "bat_kwh",
    "sep_solve_s": "solve_s",
}
METRICS = ["npc", "lcoe", "energy", "pv_kw", "bat_kwh", "solve_s"]


def read_summary(path: Path) -> dict:
    """Pull the comparison fields out of one summary.json."""
    s = json.loads(path.read_text(encoding="utf-8"))
    metrics = s.get("metrics") or {}
    run = (s.get("meta") or {}).get("run") or {}
    design = pd.DataFrame(s.get("design_by_step") or [])
    if design.empty:
        pv = bat = float("nan")
    else:
        cap = pd.to_numeric(design["installed_capacity"], errors="coerce")
        pv = float(cap[design["technology"] == "renewable"].sum())
        bat = float(cap[design["technology"] == "battery"].sum())
    return {
        "cat": path.parent.parent.name,          # <projects>/<cat>/results/summary.json
        "npc": metrics.get("objective_npc"),
        "lcoe": metrics.get("lcoe_per_kwh"),
        "energy": metrics.get("served_energy_discounted_kwh"),
        "pv_kw": pv,
        "bat_kwh": bat,
        "solve_s": run.get("solve_seconds"),
        "code_version": run.get("code_version"),
        # present only for runs with the battery-ageing model (summary.json from PR "export battery life")
        "bat_life_y": (run.get("battery_degradation") or {}).get("calendar_lifetime_years_used"),
        "cell_temp_c": (run.get("battery_degradation") or {}).get("mean_cell_temperature_c"),
    }


def collect(new_dir: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    """All summaries under new_dir, plus {cluster: first line of error.txt} for failures."""
    rows = [read_summary(p) for p in sorted(new_dir.glob("*/results/summary.json"))]
    # An empty run must still have the columns, or the merge below has nothing to join on.
    new = pd.DataFrame(rows, columns=["cat"] + METRICS + ["code_version", "bat_life_y", "cell_temp_c"])
    errors = {}
    for p in sorted(new_dir.glob("*/results/error.txt")):
        if not (p.parent / "summary.json").exists():
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            errors[p.parent.parent.name] = (text.splitlines() or [""])[0][:200]
    return new, errors


def compare(new: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    ref = ref.rename(columns=REF_COLUMNS)[["cat"] + METRICS]
    both = ref.merge(new, on="cat", how="left", suffixes=("_ref", "_new"))
    for m in METRICS:
        both[f"{m}_ratio"] = pd.to_numeric(both[f"{m}_new"], errors="coerce") / pd.to_numeric(
            both[f"{m}_ref"], errors="coerce")
    both["type"] = both["cat"].str.split("_").str[0]
    return both


def report(both: pd.DataFrame, errors: dict[str, str]) -> None:
    solved = both["npc_new"].notna()
    print(f"clusters in reference: {len(both)} | solved in new run: {int(solved.sum())} | "
          f"with error.txt: {len(errors)}")
    pending = [c for c in both.loc[~solved, "cat"] if c not in errors]
    if pending:
        print(f"no result yet ({len(pending)}):", ", ".join(pending))
    if errors:
        # Group identical messages: one cause usually explains many failures.
        print("errors, grouped by message:")
        for msg, cats in pd.Series(errors).groupby(lambda c: errors[c]).groups.items():
            print(f"  [{len(cats)}] {msg}\n       e.g. {', '.join(list(cats)[:5])}")
    if not solved.any():
        return
    versions = both.loc[solved, "code_version"].value_counts().to_dict()
    print("code_version of new results:", versions)

    # Demand sanity check: served energy must be identical if only the battery model changed.
    drift = (both.loc[solved, "energy_ratio"] - 1).abs()
    n_drift = int((drift > 1e-6).sum())
    print(f"served energy identical to reference: {int(solved.sum()) - n_drift}/{int(solved.sum())}"
          + ("" if n_drift == 0 else "  <-- demand differs, check before reading the ratios"))

    cols = ["npc_ratio", "lcoe_ratio", "pv_kw_ratio", "bat_kwh_ratio", "solve_s_ratio"]
    table = both[solved].groupby("type")[cols].describe().loc[:, (slice(None), ["min", "50%", "max"])]
    print("\nratio new / reference, by cluster type (min, median, max):")
    print(table.round(3).to_string())
    s_new, s_ref = both.loc[solved, "solve_s_new"], both.loc[solved, "solve_s_ref"]
    print(f"\nsolve time new: median {s_new.median():.0f} s, p90 {s_new.quantile(0.9):.0f} s, "
          f"max {s_new.max():.0f} s | reference median {s_ref.median():.0f} s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--new", required=True, type=Path, help="projects folder of the new run")
    ap.add_argument("--ref", required=True, type=Path, help="reference table (compare_table.csv)")
    ap.add_argument("--out", type=Path, help="write the joined table here (CSV)")
    args = ap.parse_args(argv)

    if not args.new.is_dir():
        sys.exit(f"not a folder: {args.new}")
    new, errors = collect(args.new)
    if new.empty and not errors:
        sys.exit(f"no summary.json or error.txt under {args.new}/*/results/")
    both = compare(new, pd.read_csv(args.ref))
    report(both, errors)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        both.to_csv(args.out, index=False)
        print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
