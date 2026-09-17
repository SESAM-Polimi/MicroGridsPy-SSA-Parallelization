"""
multi_year_core.py — LEAN, cluster-side export for the multi-year model.

Writes only the irreducible core needed to reconstruct every other result offline:

  summary.json   meta + metrics (NPC/LCOE/investment) + optimal sizing
                 (design_by_step: per technology x investment step, incl. the
                  battery inverter; capacity_by_year: per technology x year)
  dispatch.<fmt> full optimal dispatch / energy balance per period x year x
                 scenario, including battery SOC and battery-inverter power.

Everything else the full export produces (energy_balance, kpis, cashflows,
scenario_costs, inverter metrics, the multi-section reporting_summary, the Excel
workbook) is a deterministic post-process of {summary.json, dispatch, inputs} and
is rebuilt OFFLINE by the reproduction library — not on the cluster.

This module deliberately reuses the existing builders from
`core.export.multi_year_results` (single source of truth for the physics/economics)
and only adds the lean summary + writer on top. It imports no GUI/plotting code.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import xarray as xr

from core.io.jsonio import write_json
from core.export.common import ensure_results_dir, safe_float
from core.multi_year_model.params import get_params
from core.multi_year_model.lifecycle import year_ordinal
from core.export.multi_year_results import (
    build_dispatch_timeseries_table_multi_year,
    build_design_by_step_table_multi_year,
    build_capacity_by_year_table_multi_year,
    build_investment_summary_table_multi_year,
    _scenario_weights,
)


# --- dispatch column selection -------------------------------------------------
# The full dispatch table carries many derived/diagnostic columns (delivered
# duplicates, loss/fade/soh internals, balance residuals) that are recomputable
# offline. Keep only the meaningful energy-balance components + battery state.
_CORE_DISPATCH_HEAD = ["period", "year", "scenario", "load_demand", "res_generation_total"]
_CORE_DISPATCH_TAIL = [
    "generator_generation",
    "battery_charge",
    "battery_discharge",
    "battery_soc",
    "battery_inverter_active_power",
    "lost_load",
    "grid_import",
    "grid_export",
]


def _core_dispatch_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Trim the dispatch table to the core energy-balance columns (schema-stable:
    a column is kept whenever it exists, even if the technology is inactive)."""
    res_cols = [c for c in df.columns if str(c).startswith("res_generation__")]
    ordered = _CORE_DISPATCH_HEAD + res_cols + _CORE_DISPATCH_TAIL
    keep = [c for c in ordered if c in df.columns]
    return df[keep].copy()


def _json_scalar(value: Any) -> Any:
    """Coerce a cell to a JSON-native scalar (finite float / int / str / bool / None)."""
    if value is None:
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        fv = float(value)
        return fv if math.isfinite(fv) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (int, str)):
        return value
    return str(value)


def _df_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame -> list of JSON-safe row dicts (NaN/inf -> null)."""
    if df is None or df.empty:
        return []
    safe = df.where(pd.notnull(df), None)
    return [{k: _json_scalar(v) for k, v in rec.items()} for rec in safe.to_dict(orient="records")]


def _run_meta(
    sets: xr.Dataset,
    data: xr.Dataset,
    *,
    project_name: str,
    status: Optional[str] = None,
    solver: Optional[str] = None,
    objective_value: Optional[float] = None,
    run_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Best-effort run provenance for summary.json (never raises on odd configs).

    `run_info` is caller-supplied provenance (solver options, timings, code version,
    pipeline settings...). It is stored verbatim under meta["run"]; the engine does
    not interpret it.
    """
    settings = (data.attrs or {}).get("settings", {}) or {}
    years = [str(y) for y in sets.coords["year"].values.tolist()]
    inv_steps = [str(s) for s in sets.coords["inv_step"].values.tolist()]
    step_start: Dict[str, str] = {}
    if "inv_step_start_year" in sets:
        step_start = {
            str(s): str(sets["inv_step_start_year"].sel(inv_step=s).item())
            for s in sets.coords["inv_step"].values
        }
    scenarios = [str(s) for s in sets.coords["scenario"].values.tolist()]
    try:
        p = get_params(data)
        w = _scenario_weights(p, sets.coords["scenario"])
        weights = [float(w.sel(scenario=s)) for s in sets.coords["scenario"].values]
    except Exception:
        weights = []
    resources = (
        [str(r) for r in data.coords["resource"].values.tolist()]
        if "resource" in data.coords
        else []
    )
    return {
        "project_name": project_name,
        "formulation": str(settings.get("formulation", "dynamic")),
        "system_type": str(settings.get("system_type", "")),
        "on_grid": bool(settings.get("on_grid", False)),
        "grid_allow_export": bool(settings.get("grid_allow_export", settings.get("allow_export", False))),
        "social_discount_rate": float(settings.get("social_discount_rate", 0.0) or 0.0),
        "start_year": str(settings.get("start_year_label", years[0] if years else "")),
        "horizon_years": len(years),
        "years": years,
        "inv_steps": inv_steps,
        "inv_step_start_years": step_start,
        "scenarios": scenarios,
        "scenario_weights": weights,
        "resources": resources,
        "solver": solver,
        "status": status,
        "objective_value": _json_scalar(safe_float(objective_value)),
        "run": dict(run_info or {}),
    }


def build_summary_metrics(
    *,
    sets: xr.Dataset,
    data: xr.Dataset,
    design_df: pd.DataFrame,
    dispatch_df: pd.DataFrame,
    objective_value: Optional[float],
) -> Dict[str, float]:
    """Headline economics without the heavy per-year cost builders.

    NPC   = optimized objective value (expected, annuity-based).
    LCOE  = NPC / discounted expected served energy, where
            served(year, scenario)  = sum_period(load_demand - lost_load)
            expected served(year)    = sum_scenario weight * served
            discounted               = sum_year served * 1/(1+r)^ordinal(year).
    Investment (nominal / present) reuses build_investment_summary_table_multi_year.
    """
    p = get_params(data)
    investment = build_investment_summary_table_multi_year(sets=sets, data=data, design_df=design_df)
    inv_nominal = float(pd.to_numeric(investment.get("Nominal investment cost"), errors="coerce").fillna(0.0).sum())
    inv_present = float(pd.to_numeric(investment.get("Present-value investment cost"), errors="coerce").fillna(0.0).sum())

    npc = float(safe_float(objective_value))

    rs = float(p.settings.get("social_discount_rate", 0.0) or 0.0)
    disc = 1.0 / ((1.0 + rs) ** year_ordinal(sets))
    disc_map = {str(y): float(disc.sel(year=y)) for y in sets.coords["year"].values}
    w = _scenario_weights(p, sets.coords["scenario"])
    w_map = {str(s): float(w.sel(scenario=s)) for s in w.coords["scenario"].values}

    served = (
        dispatch_df.groupby(["year", "scenario"], as_index=False)
        .agg(load=("load_demand", "sum"), lost=("lost_load", "sum"))
    )
    served["served"] = served["load"] - served["lost"]
    served["weight"] = served["scenario"].astype(str).map(w_map).fillna(0.0)
    served["disc"] = served["year"].astype(str).map(disc_map).fillna(0.0)
    discounted_energy = float((served["served"] * served["weight"] * served["disc"]).sum())

    lcoe = npc / discounted_energy if discounted_energy > 1e-12 else float("nan")
    return {
        "objective_npc": npc if math.isfinite(npc) else None,
        "lcoe_per_kwh": lcoe if math.isfinite(lcoe) else None,
        "investment_nominal": inv_nominal,
        "investment_present": inv_present,
        "served_energy_discounted_kwh": discounted_energy,
    }


def _write_dispatch(df: pd.DataFrame, out_dir: Path, dispatch_format: str) -> tuple[Optional[Path], str]:
    """Write the trimmed dispatch. Parquet by default (small + fast); fall back to
    CSV if parquet support (pyarrow/fastparquet) is unavailable on the node."""
    fmt = str(dispatch_format or "parquet").lower()
    if fmt == "none":
        return None, "none"  # summary.json only (metrics are computed before this)
    if fmt == "parquet":
        try:
            path = out_dir / "dispatch.parquet"
            df.to_parquet(path, index=False)
            return path, "parquet"
        except Exception:
            fmt = "csv"  # graceful fallback — never fail a run over the container format
    path = out_dir / "dispatch.csv"
    df.to_csv(path, index=False)
    return path, "csv"


def export_core_outputs(
    project_name: str,
    sets: xr.Dataset,
    data: xr.Dataset,
    model: Optional[Any],
    vars: Dict[str, Any],
    solution: Optional[xr.Dataset],
    *,
    out_dir: Path | None = None,
    objective_value: Optional[float] = None,
    status: Optional[str] = None,
    solver: Optional[str] = None,
    dispatch_format: str = "parquet",
    run_info: Optional[Dict[str, Any]] = None,
) -> dict:
    """Write the lean core bundle (summary.json + dispatch.<fmt>) and return paths.

    Builds the dispatch table exactly once (the full export rebuilds it 4-5x via the
    kpis/inverter/cashflow builders); skips every derived table and the Excel workbook.
    """
    if out_dir is None:
        out_dir = ensure_results_dir(project_name)
    else:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    obj = objective_value
    if obj is None and model is not None and hasattr(model, "objective"):
        obj = safe_float(getattr(model.objective, "value", None))

    design = build_design_by_step_table_multi_year(sets=sets, data=data, vars=vars, solution=solution)
    capacity = build_capacity_by_year_table_multi_year(sets=sets, design_df=design)
    dispatch = build_dispatch_timeseries_table_multi_year(sets=sets, data=data, vars=vars, solution=solution)
    dispatch_core = _core_dispatch_columns(dispatch)

    metrics = build_summary_metrics(
        sets=sets, data=data, design_df=design, dispatch_df=dispatch, objective_value=obj
    )
    meta = _run_meta(
        sets, data, project_name=project_name, status=status, solver=solver,
        objective_value=obj, run_info=run_info,
    )

    summary = {
        "meta": meta,
        "metrics": metrics,
        "design_by_step": _df_records(design),
        "capacity_by_year": _df_records(capacity),
    }

    written: Dict[str, str] = {"out_dir": str(out_dir)}
    summary_path = out_dir / "summary.json"
    write_json(summary_path, summary)
    written["summary_json"] = str(summary_path)

    dispatch_path, used_fmt = _write_dispatch(dispatch_core, out_dir, dispatch_format)
    written["dispatch"] = str(dispatch_path) if dispatch_path is not None else ""
    written["dispatch_format"] = used_fmt
    return written
