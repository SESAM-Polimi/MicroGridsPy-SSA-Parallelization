"""
run_cluster.py — NEW-engine replacement for the old run_nostreamlit_update.run_yaml.

Per cluster it:
  1. scaffolds projects/<cat>/inputs (config_map.build_project: formulation.json + YAMLs
     + demand/resource CSVs from the old archetype/PVGIS science);
  2. builds + solves the MultiYearModel with the requested solver (HiGHS by default);
  3. exports the results bundle to projects/<cat>/results/ (design_by_step.csv,
     reporting_summary.csv [holds LCOE], kpis_yearly.csv, ...).

Completion marker: results/reporting_summary.csv  (replaces the old costs.csv).

The NEW engine resolves `projects/<name>` against the process CWD, so this module
chdir's to the repo root (the folder containing `projects/`) before building/solving.
That makes local (ProcessPool) and HPC (SGE task) runs behave identically.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mgpy2.paths import ensure_engines_importable, repo_root
from mgpy2.config_map import ThesisConfig, build_project

COMPLETION_MARKER = "reporting_summary.csv"


@dataclass
class RunResult:
    cat: str
    status: str            # "ok" | "skip" | "infeasible" | "error"
    objective: Optional[float] = None
    lcoe: Optional[float] = None
    message: str = ""


def _project_name_from_row(row: dict) -> str:
    ensure_engines_importable()
    from core.io.utils import sanitize_project_name
    return sanitize_project_name(str(row["cat"]))


def _read_lcoe(results_dir: Path) -> Optional[float]:
    import pandas as pd
    rs = results_dir / "reporting_summary.csv"
    if not rs.exists():
        return None
    try:
        df = pd.read_csv(rs)
        mask = df.apply(lambda r: r.astype(str).str.contains("LCOE", case=False).any(), axis=1)
        hit = df[mask]
        if hit.empty:
            return None
        # 'value' column holds the number in the new reporting_summary schema
        for col in ("value", "Value"):
            if col in hit.columns:
                return float(pd.to_numeric(hit[col], errors="coerce").dropna().iloc[0])
    except Exception:
        return None
    return None


def _status_ok(status: str, objective: Optional[float]) -> bool:
    """Success if the solver did not report infeasibility and we have a finite objective.
    linopy/HiGHS report status 'ok' (not 'optimal'); infeasible runs report 'warning'
    with objective NaN, so a finite objective is the reliable signal."""
    import math
    t = str(status or "").lower()
    if "infeasible" in t:
        return False
    if objective is not None:
        try:
            if math.isfinite(float(objective)):
                return True
        except (TypeError, ValueError):
            pass
    return ("optimal" in t) or ("feasible" in t) or (t.strip() == "ok")


def run_cluster(row: dict, cfg: Optional[ThesisConfig] = None, *,
                solver: str = "highs", solver_params: Optional[dict] = None,
                skip_if_done: bool = True, prepare: bool = True, solve: bool = True) -> RunResult:
    """Run one cluster on the new engine. `row` is a dict from advanced_sample.csv.

    prepare/solve let you split the two stages for HPC use:
      * prepare only (login node, needs internet for PVGIS): run_cluster(..., solve=False)
      * solve only  (compute node, no internet): run_cluster(..., prepare=False)
    """
    cfg = cfg or ThesisConfig()
    os.chdir(repo_root())  # so project_paths() (cwd/projects) matches our projects dir

    ensure_engines_importable()
    from core.io.utils import project_paths
    from core.multi_year_model.model import MultiYearModel
    from core.export.multi_year_results import export_multi_year_results

    cat = str(row["cat"])
    name = _project_name_from_row(row)
    paths = project_paths(name)

    if skip_if_done and (paths.results_dir / COMPLETION_MARKER).exists():
        return RunResult(cat, "skip", message="results already exist")

    if prepare:
        try:
            build_project(name, row, cfg, overwrite=True)
        except Exception as e:
            _write_error(paths.results_dir, f"input prep failed: {e}")
            return RunResult(cat, "error", message=f"prep: {e}")
        if not solve:
            return RunResult(cat, "prepared", message="inputs written (prepare-only)")
    elif not (paths.inputs_dir / "load_demand.csv").exists():
        _write_error(paths.results_dir, "solve-only requested but inputs are missing (run prepare first)")
        return RunResult(cat, "error", message="missing inputs for solve-only")

    try:
        model = MultiYearModel(project_name=name)
        sol = model.solve_single_objective(
            solver=solver,
            solver_params=solver_params or {},
            log_file_path=paths.logs_dir / f"{solver}_solve.log",
        )
        status = str(sol.attrs.get("status", ""))
        obj = sol.attrs.get("objective_value")

        if not _status_ok(status, obj):
            _write_error(paths.results_dir, f"solve not optimal: status={status}")
            return RunResult(cat, "infeasible", objective=obj, message=f"status={status}")

        export_multi_year_results(
            name, model.sets, model.data, model.model, model.vars,
            getattr(model.model, "solution", None), out_dir=paths.results_dir,
        )
        lcoe = _read_lcoe(paths.results_dir)
        return RunResult(cat, "ok", objective=float(obj) if obj is not None else None, lcoe=lcoe)
    except Exception as e:
        import traceback
        _write_error(paths.results_dir, f"solve/export failed: {e}\n{traceback.format_exc()}")
        return RunResult(cat, "error", message=f"solve: {e}")


def _write_error(results_dir: Path, msg: str) -> None:
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "error.txt").write_text(str(msg), encoding="utf-8")
    except Exception:
        pass


# CLI: run a single row from advanced_sample.csv by 1-based task id (SGE-friendly)
if __name__ == "__main__":
    import argparse
    from mgpy2.sample import load_sample, row_for_task, sample_path    
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="sample file (default: $SAMPLE_CSV)")
    ap.add_argument("--task-id", type=int, help="1-based row index (excludes // comment rows)")
    ap.add_argument("--cat", help="run a specific cluster id instead of --task-id")
    ap.add_argument("--solver", default="highs")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--include-generator", action="store_true",
                    help="add a diesel backup (feasibility option)")
    ap.add_argument("--max-lost-load", type=float, default=0.0,
                    help="max lost-load fraction >0 (feasibility option for PV+battery only)")
    ap.add_argument("--lost-load-cost", type=float, default=0.0)
    ap.add_argument("--prepare-only", action="store_true",
                    help="write inputs only (PVGIS/demand); run on a node WITH internet")
    ap.add_argument("--solve-only", action="store_true",
                    help="skip input prep and solve existing inputs; for offline compute nodes")
    args = ap.parse_args()

    cfg = ThesisConfig(
        time_horizon_years=args.horizon,
        include_generator=args.include_generator,
        max_lost_load_fraction=args.max_lost_load,
        lost_load_cost_per_kwh=args.lost_load_cost,
    )

    csv_path = sample_path(args.csv)
    df = load_sample(csv_path)
    if args.cat:
        sub = df[df["cat"].astype(str) == args.cat]
        if sub.empty:
            raise SystemExit(f"cat {args.cat} not found in {csv_path}")        
        row = sub.iloc[0].to_dict()
    else:
        if args.task_id is None:
            raise SystemExit("give --task-id or --cat")
        row = row_for_task(df, args.task_id)

    res = run_cluster(row, cfg=cfg, solver=args.solver,
                      prepare=not args.solve_only, solve=not args.prepare_only)
    print(f"[{res.cat}] {res.status} obj={res.objective} lcoe={res.lcoe} {res.message}")
    raise SystemExit(0 if res.status in ("ok", "skip", "prepared") else 1)
