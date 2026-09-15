"""
reporting.py — OFFLINE reproduction library for the lean HPC bundle.

The cluster now writes only the core bundle per cluster (see
core.export.multi_year_core.export_core_outputs):

    projects/<cat>/results/summary.json      meta + metrics + sizing
    projects/<cat>/results/dispatch.parquet  full optimal dispatch (or .csv)

This module rebuilds the COMPLETE legacy result set locally, reusing the engine's
own builders (`core.export.multi_year_results.build_multi_year_results`) as the
single source of truth. Two methods:

  method="reconstruct"  (fast, no solve)  reconstruct a linopy-style solution
        Dataset from {summary.json, dispatch, inputs}, then run the builders.
        EXACT for systems without a generator (fuel/opex has no free operational
        term to recover); guarded otherwise. This is the intended fast path.

  method="resolve"  (exact, slower)  re-solve the model from the inputs and run
        the full export. No fidelity assumptions; use to validate reconstruct or
        for generator systems / anything reconstruct refuses.

Both funnel through the same `build_multi_year_results`, so every downstream table
(energy_balance, kpis, cashflows, scenario_costs, inverter metrics, the full
reporting_summary, the Excel workbook) is identical to what the full cluster export
would have produced.

Nothing here runs on the cluster.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from mgpy2.paths import ensure_engines_importable, projects_root


# =============================================================================
# Core-bundle loading (pure; no engine import required)
# =============================================================================
@dataclass
class CoreResults:
    cat: str
    results_dir: Path
    meta: Dict[str, Any]
    metrics: Dict[str, Any]
    design_by_step: pd.DataFrame
    capacity_by_year: pd.DataFrame
    dispatch: pd.DataFrame
    dispatch_path: Path


def _results_dir(cat: str, projects: Path) -> Path:
    return projects / str(cat) / "results"


def _read_dispatch(results_dir: Path) -> tuple[pd.DataFrame, Path]:
    pq = results_dir / "dispatch.parquet"
    csv = results_dir / "dispatch.csv"
    if pq.exists():
        return pd.read_parquet(pq), pq
    if csv.exists():
        return pd.read_csv(csv), csv
    raise FileNotFoundError(f"No dispatch.parquet or dispatch.csv in {results_dir}")


def load_core(cat: str, projects: Optional[Path] = None) -> CoreResults:
    """Load the lean bundle for one cluster. Offline, no engine needed."""
    projects = projects or projects_root()
    rd = _results_dir(cat, projects)
    summary = json.loads((rd / "summary.json").read_text(encoding="utf-8"))
    dispatch, dpath = _read_dispatch(rd)
    return CoreResults(
        cat=str(cat),
        results_dir=rd,
        meta=summary.get("meta", {}) or {},
        metrics=summary.get("metrics", {}) or {},
        design_by_step=pd.DataFrame(summary.get("design_by_step", []) or []),
        capacity_by_year=pd.DataFrame(summary.get("capacity_by_year", []) or []),
        dispatch=dispatch,
        dispatch_path=dpath,
    )


def energy_balance(cat: str, projects: Optional[Path] = None) -> pd.DataFrame:
    """Rebuild the energy balance directly from the persisted dispatch.

    Pure function of the dispatch table (no model rebuild, no solve) — exact."""
    ensure_engines_importable()
    from core.export.multi_year_results import build_energy_balance_table_multi_year
    core = load_core(cat, projects)
    return build_energy_balance_table_multi_year(core.dispatch)


# =============================================================================
# Solution reconstruction (the only new physics-facing logic)
# =============================================================================
def _project_name(cat: str) -> str:
    ensure_engines_importable()
    from core.io.utils import sanitize_project_name
    return sanitize_project_name(str(cat))


def _sets_and_data(name: str):
    """Rebuild sets + data (params) from the project inputs WITHOUT solving."""
    ensure_engines_importable()
    from core.multi_year_model.model import MultiYearModel
    m = MultiYearModel(project_name=name)
    m._initialize_data()  # builds sets + data only; no vars/constraints/objective/solve
    return m, m.sets, m.data


def _period_da(df: pd.DataFrame, col: str, sets) -> "Any":
    """Build a (period, year, scenario) DataArray from a long dispatch column,
    aligning on the sets coords (missing entries filled with 0)."""
    import xarray as xr
    period_vals = [int(p) for p in sets.coords["period"].values]
    year_vals = [str(y) for y in sets.coords["year"].values]
    scen_vals = [str(s) for s in sets.coords["scenario"].values]

    if col in df.columns:
        d = df[["period", "year", "scenario", col]].copy()
    else:
        d = df[["period", "year", "scenario"]].copy()
        d[col] = 0.0
    d["period"] = d["period"].astype(int)
    d["year"] = d["year"].astype(str)
    d["scenario"] = d["scenario"].astype(str)

    idx = pd.MultiIndex.from_product([period_vals, year_vals, scen_vals],
                                     names=["period", "year", "scenario"])
    series = d.set_index(["period", "year", "scenario"])[col]
    series = series[~series.index.duplicated(keep="last")].reindex(idx, fill_value=0.0)
    arr = series.to_numpy(dtype="float64").reshape(len(period_vals), len(year_vals), len(scen_vals))
    return xr.DataArray(
        arr,
        dims=("period", "year", "scenario"),
        coords={"period": sets.coords["period"], "year": sets.coords["year"], "scenario": sets.coords["scenario"]},
    )


def _res_generation_da(df: pd.DataFrame, sets) -> "Any":
    """Build res_generation (period, year, scenario, resource) from res_generation__<r> columns."""
    import xarray as xr
    layers = []
    for r in sets.coords["resource"].values:
        col = f"res_generation__{r}"
        layers.append(_period_da(df, col, sets))
    stacked = xr.concat(layers, dim="resource")
    stacked = stacked.assign_coords(resource=sets.coords["resource"]).transpose(
        "period", "year", "scenario", "resource"
    )
    return stacked


def _inv_step_vector(design: pd.DataFrame, technology: str, value_col: str, sets) -> "Any":
    """Build an (inv_step,) DataArray from a design_by_step column for one technology."""
    import xarray as xr
    inv_vals = [str(s) for s in sets.coords["inv_step"].values]
    out = np.zeros(len(inv_vals), dtype="float64")
    rows = design[design["technology"].astype(str) == technology]
    for _, row in rows.iterrows():
        key = str(row.get("inv_step"))
        if key in inv_vals:
            out[inv_vals.index(key)] = float(pd.to_numeric(pd.Series([row.get(value_col, 0.0)]), errors="coerce").fillna(0.0).iloc[0])
    return xr.DataArray(out, dims=("inv_step",), coords={"inv_step": sets.coords["inv_step"]})


def _res_units_da(design: pd.DataFrame, sets) -> "Any":
    import xarray as xr
    inv_vals = [str(s) for s in sets.coords["inv_step"].values]
    res_vals = [str(r) for r in sets.coords["resource"].values]
    mat = np.zeros((len(inv_vals), len(res_vals)), dtype="float64")
    rows = design[design["technology"].astype(str) == "renewable"]
    for _, row in rows.iterrows():
        i = str(row.get("inv_step"))
        j = str(row.get("resource"))
        if i in inv_vals and j in res_vals:
            mat[inv_vals.index(i), res_vals.index(j)] = float(
                pd.to_numeric(pd.Series([row.get("units", 0.0)]), errors="coerce").fillna(0.0).iloc[0]
            )
    return xr.DataArray(
        mat, dims=("inv_step", "resource"),
        coords={"inv_step": sets.coords["inv_step"], "resource": sets.coords["resource"]},
    )


def _reconstruct_solution(sets, data, core: CoreResults):
    """Reconstruct a solution xr.Dataset from the core bundle so the engine's
    builders run unchanged. Raises if the config makes reconstruction lossy."""
    ensure_engines_importable()
    import xarray as xr
    from core.multi_year_model.params import get_params

    design = core.design_by_step
    disp = core.dispatch

    # --- fidelity guard: generator opex/fuel is not recoverable from the lean bundle
    gen_units_total = 0.0
    if not design.empty and "technology" in design.columns:
        g = design[design["technology"].astype(str) == "generator"]["units"]
        gen_units_total = float(pd.to_numeric(g, errors="coerce").fillna(0.0).sum()) if not g.empty else 0.0
    gen_dispatch = float(pd.to_numeric(disp.get("generator_generation", pd.Series([0.0])), errors="coerce").fillna(0.0).abs().sum())
    if gen_units_total > 1e-9 or gen_dispatch > 1e-9:
        raise ReconstructionError(
            "Cluster has an active generator: fuel_consumption / operating cost cannot be "
            "reconstructed from the lean bundle (fuel is not stored). Use method='resolve', "
            "or add 'fuel_consumption' to the core dispatch columns before running."
        )

    p = get_params(data)
    sol: Dict[str, Any] = {}

    # sizing (inv_step[, resource]) — exact, from design_by_step
    sol["res_units"] = _res_units_da(design, sets)
    sol["battery_units"] = _inv_step_vector(design, "battery", "units", sets)
    sol["battery_inverter_power"] = _inv_step_vector(design, "battery", "installed_inverter_capacity_ac", sets)
    sol["generator_units"] = _inv_step_vector(design, "generator", "units", sets)

    # operational (period, year, scenario) — from dispatch (inv_step summed away upstream)
    sol["res_generation"] = _res_generation_da(disp, sets)
    sol["generator_generation"] = _period_da(disp, "generator_generation", sets)
    sol["battery_charge"] = _period_da(disp, "battery_charge", sets)
    sol["battery_discharge"] = _period_da(disp, "battery_discharge", sets)
    sol["battery_soc"] = _period_da(disp, "battery_soc", sets)
    sol["lost_load"] = _period_da(disp, "lost_load", sets)

    # fuel_consumption (period, year, scenario, inv_step): zero here (no generator, guarded above)
    zero_ps = _period_da(disp, "__zeros__", sets)  # all-zero (column absent -> 0)
    sol["fuel_consumption"] = zero_ps.expand_dims(inv_step=sets.coords["inv_step"]).transpose(
        "period", "year", "scenario", "inv_step"
    )

    # grid variables only when the system is on-grid
    if p.is_grid_on():
        sol["grid_import"] = _period_da(disp, "grid_import", sets)
        if p.is_grid_export_enabled():
            sol["grid_export"] = _period_da(disp, "grid_export", sets)

    return xr.Dataset(sol)


class ReconstructionError(RuntimeError):
    pass


# =============================================================================
# Reproduction — the public entry point
# =============================================================================
def reproduce(
    cat: str,
    *,
    method: str = "reconstruct",
    projects: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    write: bool = False,
    solver: str = "highs",
):
    """Rebuild the complete result set for one cluster and return a MultiYearResults.

    method="reconstruct" (default): fast, from the lean bundle (no solve).
    method="resolve": exact, re-solves the model from the inputs.
    write=True also writes the full legacy CSV+Excel bundle to out_dir.
    """
    ensure_engines_importable()
    from core.export.multi_year_results import (
        build_multi_year_results,
        export_multi_year_results_package,
    )

    name = _project_name(cat)
    m, sets, data = _sets_and_data(name)

    if method == "resolve":
        sol_status = m.solve_single_objective(solver=solver)
        solution = getattr(m.model, "solution", None)
        vars_dict = m.vars
        objective_value = sol_status.attrs.get("objective_value")
        status = str(sol_status.attrs.get("status", ""))
    elif method == "reconstruct":
        core = load_core(cat, projects)
        solution = _reconstruct_solution(sets, data, core)
        vars_dict = None
        objective_value = core.metrics.get("objective_npc")
        status = "reconstructed"
    else:
        raise ValueError(f"unknown method {method!r} (use 'reconstruct' or 'resolve')")

    results = build_multi_year_results(
        project_name=name,
        sets=sets,
        data=data,
        vars=vars_dict,
        solution=solution,
        objective_value=objective_value,
        status=status,
        solver=solver if method == "resolve" else None,
        results_dir=out_dir,
        source=f"reporting:{method}",
    )

    if write:
        target = Path(out_dir) if out_dir else _results_dir(cat, projects or projects_root())
        export_multi_year_results_package(results=results, out_dir=target)
    return results


# =============================================================================
# Validation helper — prove reconstruct == resolve in the user's environment
# =============================================================================
def validate(cat: str, *, projects: Optional[Path] = None, solver: str = "highs") -> Dict[str, float]:
    """Reconstruct and resolve the same cluster and report max abs differences on
    the headline tables. Run this once in the real (mgpy) env to trust reconstruct."""
    rec = reproduce(cat, method="reconstruct", projects=projects)
    res = reproduce(cat, method="resolve", projects=projects, solver=solver)

    def _num(df: pd.DataFrame) -> pd.DataFrame:
        return df.select_dtypes(include=[np.number]).reset_index(drop=True)

    def _maxdiff(a: pd.DataFrame, b: pd.DataFrame) -> float:
        an, bn = _num(a), _num(b)
        cols = [c for c in an.columns if c in bn.columns]
        if not cols or len(an) != len(bn):
            return float("nan")
        return float((an[cols] - bn[cols]).abs().to_numpy().max())

    return {
        "design_by_step": _maxdiff(rec.design_by_step, res.design_by_step),
        "capacity_by_year": _maxdiff(rec.capacity_by_year, res.capacity_by_year),
        "dispatch": _maxdiff(rec.dispatch, res.dispatch),
        "energy_balance": _maxdiff(rec.energy_balance, res.energy_balance),
        "kpis_yearly": _maxdiff(rec.kpis_yearly, res.kpis_yearly),
        "cashflows_discounted": _maxdiff(rec.cashflows_discounted, res.cashflows_discounted),
        "reporting_summary": _maxdiff(rec.reporting_summary, res.reporting_summary),
    }


# =============================================================================
# CLI
# =============================================================================
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Rebuild full results from the lean bundle (offline).")
    ap.add_argument("--cat", required=True, help="cluster id (as in advanced_sample.csv)")
    ap.add_argument("--method", default="reconstruct", choices=["reconstruct", "resolve"])
    ap.add_argument("--out", default=None, help="write the full CSV+Excel bundle here")
    ap.add_argument("--validate", action="store_true", help="reconstruct vs resolve max-diff report")
    ap.add_argument("--solver", default="highs")
    args = ap.parse_args()

    if args.validate:
        report = validate(args.cat, solver=args.solver)
        for k, v in report.items():
            print(f"  {k:24s} max|Δ| = {v:.3e}")
        raise SystemExit(0)

    out = Path(args.out) if args.out else None
    results = reproduce(args.cat, method=args.method, out_dir=out, write=bool(out), solver=args.solver)
    print(f"[{args.cat}] rebuilt via {args.method}: "
          f"{len(results.dispatch)} dispatch rows, "
          f"{len(results.reporting_summary)} reporting rows"
          + (f" -> {out}" if out else ""))
