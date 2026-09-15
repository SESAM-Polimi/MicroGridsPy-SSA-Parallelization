#!/usr/bin/env python3
"""
orchestrator.py — LOCAL parallel runner on the NEW MicroGridsPy engine.

Reads the country sample ($SAMPLE_CSV or --csv, via mgpy2.sample) and runs each
cluster via mgpy2.run_cluster in a ProcessPool. Each worker chdir's to the repo
root (handled inside run_cluster) so `projects/<cat>` resolves consistently.

Usage:
    python orchestrator.py --csv data/sample_input_2025/ETH/advanced_sample.csv --workers 3

Completion marker is results/summary.json.
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from mgpy2.config_map import ThesisConfig
from mgpy2.run_cluster import run_cluster
from mgpy2.sample import load_sample, sample_path


def _worker(row: dict, cfg: ThesisConfig, solver: str, prepare: bool, solve: bool):
    res = run_cluster(row, cfg=cfg, solver=solver, prepare=prepare, solve=solve)
    return (res.cat, res.status, res.objective, res.lcoe, res.message)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="sample file (default: $SAMPLE_CSV)")
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
    ap.add_argument("--export-profile", default="core", choices=["core", "full"],
                    help="core = lean bundle (summary.json + dispatch); full = legacy CSV+Excel bundle")
    ap.add_argument("--dispatch-format", default="parquet", choices=["parquet", "csv"],
                    help="container for the dispatch time series (core profile)")
    args = ap.parse_args()
    prepare, solve = (not args.solve_only), (not args.prepare_only)

    cfg = ThesisConfig(
        time_horizon_years=args.horizon,
        include_generator=args.include_generator,
        max_lost_load_fraction=args.max_lost_load,
        lost_load_cost_per_kwh=args.lost_load_cost,
        export_profile=args.export_profile,
        dispatch_format=args.dispatch_format,
    )

    rows = load_sample(sample_path(args.csv)).to_dict("records")

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
