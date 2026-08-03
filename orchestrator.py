#!/usr/bin/env python3
"""
orchestrator_new.py — LOCAL parallel runner on the NEW MicroGridsPy engine.

Drop-in replacement for orchestrator.py. Reads advanced_sample.csv and runs each
cluster via mgpy2.run_cluster in a ProcessPool. Each worker chdir's to the repo
root (handled inside run_cluster) so `projects/<cat>` resolves consistently.

Usage:
    python orchestrator_new.py --csv advanced_sample.csv --workers 3 --solver highs

Completion marker is results/reporting_summary.csv (skipped if present).
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from mgpy2.config_map import ThesisConfig
from mgpy2.run_cluster import run_cluster

REPO = Path(__file__).resolve().parent


def _worker(row: dict, cfg: ThesisConfig, solver: str, prepare: bool, solve: bool):
    res = run_cluster(row, cfg=cfg, solver=solver, prepare=prepare, solve=solve)
    return (res.cat, res.status, res.objective, res.lcoe, res.message)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(REPO / "advanced_sample.csv"))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--solver", default="highs")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--include-generator", action="store_true",
                    help="add a diesel backup (needed for feasibility if lost load is not allowed)")
    ap.add_argument("--max-lost-load", type=float, default=0.0,
                    help="max lost-load fraction (>0 makes PV+battery-only feasible)")
    ap.add_argument("--lost-load-cost", type=float, default=0.0)
    ap.add_argument("--prepare-only", action="store_true",
                    help="only write inputs (PVGIS/demand) in parallel; use on a login node with internet")
    ap.add_argument("--solve-only", action="store_true",
                    help="skip input prep; solve pre-staged inputs")
    args = ap.parse_args()
    prepare, solve = (not args.solve_only), (not args.prepare_only)

    cfg = ThesisConfig(
        time_horizon_years=args.horizon,
        include_generator=args.include_generator,
        max_lost_load_fraction=args.max_lost_load,
        lost_load_cost_per_kwh=args.lost_load_cost,
    )

    df = pd.read_csv(args.csv)
    df = df[~df.iloc[:, 0].astype(str).str.startswith("//")].reset_index(drop=True)
    rows = df.to_dict("records")

    print(f"Running {len(rows)} clusters on {args.workers} workers (solver={args.solver}, "
          f"horizon={args.horizon}, generator={args.include_generator}, "
          f"max_lost_load={args.max_lost_load})")

    t0 = time.time()
    counts: dict = {}
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(_worker, r, cfg, args.solver, prepare, solve) for r in rows]
        for fut in as_completed(futs):
            cat, status, obj, lcoe, msg = fut.result()
            counts[status] = counts.get(status, 0) + 1
            print(f"[{cat}] {status} lcoe={lcoe} {('- ' + msg) if msg else ''}")

    print(f"Done in {time.time()-t0:.1f}s: {counts}")


if __name__ == "__main__":
    main()
