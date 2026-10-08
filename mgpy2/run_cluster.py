"""
run_cluster.py — NEW-engine replacement for the old run_nostreamlit_update.run_yaml.

Per cluster it:
  1. scaffolds projects/<cat>/inputs (config_map.build_project: formulation.json + YAMLs
     + demand/resource CSVs from the old archetype/PVGIS science);
  2. builds + solves the MultiYearModel with the requested solver (HiGHS by default);
  3. exports results to projects/<cat>/results/. Default profile "core" writes the
     lean bundle (summary.json [meta+metrics+sizing] + dispatch.<parquet|csv>);
     profile "full" writes the legacy CSV+Excel bundle. Everything omitted by the
     core profile is rebuilt offline by mgpy2.reporting.

Completion marker: results/summary.json  (core profile; replaces reporting_summary.csv).

The NEW engine resolves `projects/<name>` against the process CWD, so this module
chdir's to the repo root (the folder containing `projects/`) before building/solving.
That makes local (ProcessPool) and HPC (SGE task) runs behave identically.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mgpy2.archetypes import load_release
from mgpy2.paths import ensure_engines_importable, repo_root, run_cwd
from mgpy2.config_map import ThesisConfig, build_project

COMPLETION_MARKER = "summary.json"


@dataclass
class RunResult:
    cat: str
    status: str            # "ok" | "skip" | "infeasible" | "error"
    objective: Optional[float] = None
    lcoe: Optional[float] = None
    message: str = ""


def _coerce(raw: str):
    """'2' -> 2, '1e-6' -> 1e-06, 'barrier' -> 'barrier' (solvers want real numbers)."""
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def parse_solver_opts(items) -> dict:
    """['Method=2', 'Crossover=0'] -> {'Method': 2, 'Crossover': 0}."""
    opts: dict = {}
    for item in items or ():
        key, sep, raw = str(item).partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--solver-opt expects KEY=VALUE, got {item!r}")
        opts[key.strip()] = _coerce(raw.strip())
    return opts


def solver_params_for(solver: str, threads: Optional[int] = None,
                      time_limit: Optional[float] = None,
                      extra: Optional[dict] = None) -> dict:
    """Translate generic limits into the option names each solver expects.

    Gurobi: Threads / TimeLimit.  HiGHS: threads / time_limit.
    Only limits that were actually given are returned (None = solver default).
    `extra` holds solver-native options (e.g. {'Method': 2}) and is applied last,
    so an explicit extra option wins over the generic ones.
    """
    names = {"gurobi": ("Threads", "TimeLimit"), "highs": ("threads", "time_limit")}
    if solver not in names:
        raise ValueError(f"no solver-parameter mapping for solver {solver!r}")
    threads_key, time_key = names[solver]
    params: dict = {}
    if threads is not None:
        params[threads_key] = int(threads)
    if time_limit is not None:
        params[time_key] = float(time_limit)
    params.update(extra or {})
    return params


def _project_name_from_row(row: dict) -> str:
    ensure_engines_importable()
    from core.io.utils import sanitize_project_name
    return sanitize_project_name(str(row["cat"]))


def _read_lcoe(results_dir: Path) -> Optional[float]:
    import pandas as pd
    # Core profile: LCOE lives in summary.json -> metrics.lcoe_per_kwh
    sj = results_dir / "summary.json"
    if sj.exists():
        try:
            import json
            metrics = (json.loads(sj.read_text(encoding="utf-8")).get("metrics") or {})
            v = metrics.get("lcoe_per_kwh")
            return float(v) if v is not None else None
        except Exception:
            return None
    # Legacy full-profile fallback: reporting_summary.csv
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


def _status_ok(status: str, objective: Optional[float], termination: Optional[str] = None) -> bool:
    """Success if the solver did not report infeasibility and we have a finite objective.
    linopy/HiGHS report status 'ok' (not 'optimal'); infeasible runs report 'warning'
    with objective NaN, so a finite objective is the reliable signal.

    When the termination condition is known it must be 'optimal'. A barrier run without
    crossover that stops early (numerical trouble, time limit) still has a finite objective,
    and would otherwise be stored as a normal result.
    """
    import math
    t = str(status or "").lower()
    if "infeasible" in t:
        return False
    if termination is not None and str(termination).strip().lower() != "optimal":
        return False
    if objective is not None:
        try:
            if math.isfinite(float(objective)):
                return True
        except (TypeError, ValueError):
            pass
    return ("optimal" in t) or ("feasible" in t) or (t.strip() == "ok")


def _head_commit_from_files(repo: Path) -> Optional[str]:
    """Short commit hash of HEAD read straight from .git, without the git program.

    The compute nodes cannot always run git (8 Oct 2026: every task of a hand-submitted
    rerun array recorded 'unknown'). Reading HEAD -> ref -> hash is three small file reads.
    Cannot tell whether the working tree is dirty, so the submit scripts' value is preferred.
    """
    git_dir = repo / ".git"
    try:
        head = (git_dir / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head[:7] or None                      # detached HEAD holds the hash itself
        ref = head.split(":", 1)[1].strip()               # e.g. refs/heads/main
        loose = git_dir / ref
        if loose.exists():
            return loose.read_text().strip()[:7] or None
        for line in (git_dir / "packed-refs").read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:      # "<hash> <ref>"; skips comments and ^peeled lines
                return parts[0][:7]
    except OSError:
        return None
    return None


def code_version() -> str:
    """Commit of the code that produced a result.

    Prefer $MGPY2_CODE_VERSION (computed ONCE by the submit script, incl. a
    '-dirty' flag): thousands of tasks must not each run `git status` on the same
    repository. Fallbacks: a read-only `git rev-parse` (takes no lock), then reading
    the hash from the .git folder directly (for nodes where git cannot run).
    """
    env = os.environ.get("MGPY2_CODE_VERSION")
    if env:
        return env
    try:
        # Keep to options that git 1.8 (the cluster's git) understands: no `-C`
        # (git >= 1.8.5) and no `--no-optional-locks` (git >= 2.15). Use cwd=
        # instead of -C; rev-parse only reads, so it never takes a lock anyway.
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root()), capture_output=True, text=True, timeout=10,
        )
        if out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return _head_commit_from_files(repo_root()) or "unknown"


def _solver_version(solver: str) -> str:
    try:
        if solver == "gurobi":
            import gurobipy
            return ".".join(str(v) for v in gurobipy.gurobi.version())
        if solver == "highs":
            import highspy
            return str(getattr(highspy, "__version__", "unknown"))
    except Exception:
        pass
    return "unknown"


# Battery-ageing values the engine works out per cluster while loading the data
# (engines/new/core/multi_year_model/data.py -> settings.battery_model.degradation_model).
# They live only in memory during the run; with the life derived per cluster, each
# summary.json must keep its own. Fields absent in a run (e.g. ageing switched off,
# or a hand-set life) are simply left out.
BATTERY_DEGRADATION_FIELDS = (
    "coefficients_enabled",
    "chemistry",
    "calendar_lifetime_mode",                    # "derived" | "user"
    "calendar_lifetime_years_derived",           # root of the fade budget, before flooring
    "calendar_lifetime_years_used",              # whole years: replacement interval and CRF
    "mean_cell_temperature_c",                   # PVGIS air temperature + enclosure rise
    "cycling_estimate",                          # "horizon_mean" | "first_year"
    "assumed_equivalent_full_cycles_per_year",
    "assumed_cycle_fade_per_year",
    "discharge_weighted_cycle_fade_coefficient",
    "calendar_fade_budget_fraction",             # > 0.70 triggers the engine's warning
    "calendar_fade_warning",
    "cycle_lifetime_to_eol_cycles",
    "end_of_life_soh",
    "cycle_life_scaling",
)


def _battery_degradation_info(data) -> dict:
    """Copy the per-cluster battery-ageing values out of the engine's data settings."""
    settings = (getattr(data, "attrs", None) or {}).get("settings") or {}
    if isinstance(settings, str):                # defensive: some engine versions serialise it
        settings = json.loads(settings)
    dm = (settings.get("battery_model") or {}).get("degradation_model") or {}
    out = {}
    for key in BATTERY_DEGRADATION_FIELDS:
        if key in dm and dm[key] is not None:
            value = dm[key]
            # numpy scalars are not JSON-serialisable; keep plain Python types
            out[key] = value.item() if hasattr(value, "item") else value
    return out


def _run_info(cfg: ThesisConfig, solver: str, solver_params: dict, status: str,
              prep_s: Optional[float], solve_s: float, data=None,
              termination: Optional[str] = None) -> dict:
    """Everything needed later to say HOW a summary.json was produced."""
    return {
        "code_version": code_version(),
        "solver": solver,
        "solver_version": _solver_version(solver),
        "solver_params": dict(solver_params),     # incl. Threads, TimeLimit, Method...
        "solver_status": status,
        "solver_termination": termination,       # "optimal" for every stored result
        "prep_seconds": None if prep_s is None else round(prep_s, 2),
        "solve_seconds": round(solve_s, 2),
        "pipeline_config": asdict(cfg),           # demand_growth_mode, costs, horizon...
        "demand_archetypes": load_release().provenance(),   # version, DOI, file checksums
        "battery_degradation": _battery_degradation_info(data),   # derived life, cell T, ...
        "host": platform.node(),
        "sge_job_id": os.environ.get("JOB_ID"),
        "sge_task_id": os.environ.get("SGE_TASK_ID"),
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def run_cluster(row: dict, cfg: Optional[ThesisConfig] = None, *,
                solver: str = "highs", solver_params: Optional[dict] = None,
                skip_if_done: bool = True, prepare: bool = True, solve: bool = True) -> RunResult:
    """Run one cluster on the new engine. `row` is a dict from advanced_sample.csv.

    prepare/solve let you split the two stages for HPC use:
      * prepare only (login node, needs internet for PVGIS): run_cluster(..., solve=False)
      * solve only  (compute node, no internet): run_cluster(..., prepare=False)
    """
    cfg = cfg or ThesisConfig()
    os.chdir(run_cwd())  # engine reads <cwd>/projects; this honours MGPY2_PROJECTS_DIR

    ensure_engines_importable()
    from core.io.utils import project_paths
    from core.multi_year_model.model import MultiYearModel
    from core.export.multi_year_results import export_multi_year_results

    cat = str(row["cat"])
    name = _project_name_from_row(row)
    paths = project_paths(name)

    if skip_if_done and (paths.results_dir / COMPLETION_MARKER).exists():
        return RunResult(cat, "skip", message="results already exist")

    prep_s: Optional[float] = None
    if prepare:
        t0 = time.perf_counter()
        try:
            build_project(name, row, cfg, overwrite=True)
            prep_s = time.perf_counter() - t0
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
        t0 = time.perf_counter()
        sol = model.solve_single_objective(
            solver=solver,
            solver_params=solver_params or {},
            log_file_path=paths.logs_dir / f"{solver}_solve.log",
        )
        solve_s = time.perf_counter() - t0   # build + solve (the engine does both here)
        status = str(sol.attrs.get("status", ""))
        termination = sol.attrs.get("termination_condition")
        obj = sol.attrs.get("objective_value")

        if not _status_ok(status, obj, termination):
            _write_error(paths.results_dir,
                         f"solve not optimal: status={status} termination={termination}")
            return RunResult(cat, "infeasible", objective=obj,
                             message=f"status={status} termination={termination}")

        export_multi_year_results(
            name, model.sets, model.data, model.model, model.vars,
            getattr(model.model, "solution", None), out_dir=paths.results_dir,
            profile=cfg.export_profile, dispatch_format=cfg.dispatch_format,
            status=status, solver=solver,
            run_info=_run_info(cfg, solver, solver_params or {}, status, prep_s, solve_s,
                               data=model.data, termination=termination),
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
    ap.add_argument("--threads", type=int, default=None,
                    help="solver threads (use 1 per SGE slot)")
    ap.add_argument("--time-limit", type=float, default=None,
                    help="solver time limit in seconds (keep below h_rt)")
    ap.add_argument("--solver-opt", action="append", default=[], metavar="KEY=VALUE",
                    help="solver-native option, repeatable (e.g. --solver-opt Method=2)")
    ap.add_argument("--include-generator", action="store_true",
                    help="add a diesel backup (feasibility option)")
    ap.add_argument("--max-lost-load", type=float, default=0.0,
                    help="max lost-load fraction >0 (feasibility option for PV+battery only)")
    ap.add_argument("--lost-load-cost", type=float, default=0.0)
    ap.add_argument("--prepare-only", action="store_true",
                    help="write inputs only (PVGIS/demand); run on a node WITH internet")
    ap.add_argument("--solve-only", action="store_true",
                    help="skip input prep and solve existing inputs; for offline compute nodes")
    ap.add_argument("--export-profile", default="core", choices=["core", "full"],
                    help="core = lean bundle (summary.json + dispatch); full = legacy CSV+Excel bundle")
    ap.add_argument("--dispatch-format", default="parquet", choices=["parquet", "csv", "none"],
                    help="container for the dispatch time series (core profile); "
                         "'none' = summary.json only")
    args = ap.parse_args()

    cfg = ThesisConfig(
        time_horizon_years=args.horizon,
        include_generator=args.include_generator,
        max_lost_load_fraction=args.max_lost_load,
        lost_load_cost_per_kwh=args.lost_load_cost,
        export_profile=args.export_profile,
        dispatch_format=args.dispatch_format,
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
                      solver_params=solver_params_for(args.solver, args.threads, args.time_limit,
                                                    extra=parse_solver_opts(args.solver_opt)),
                      prepare=not args.solve_only, solve=not args.prepare_only)
    print(f"[{res.cat}] {res.status} obj={res.objective} lcoe={res.lcoe} {res.message}")
    raise SystemExit(0 if res.status in ("ok", "skip", "prepared") else 1)
