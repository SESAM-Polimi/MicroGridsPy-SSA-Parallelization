"""
config_map.py — map the OLD monolithic a.yaml (+ per-cluster sample row) onto the
NEW engine's split project inputs, and scaffold a solvable project.

Strategy (lowest-risk): reuse the engine's own `write_templates()` to lay down
structurally-correct files (formulation.json is written separately, mirroring the
Project Setup page), then patch the techno-economic values we can map cleanly.

Thesis structural mapping (from Data_sheet/a.yaml):
  core_formulation      dynamic            (multi-year, matches 20-yr horizon + growth)
  system_type           off_grid           (grid_connection=false)
  time_horizon_years    20                 (project_settings.time_horizon)
  start_year_label      "2025"             (start_date 2025-01-01; run_yaml used 2025)
  social_discount_rate  0.1                (project_settings.discount_rate)
  capacity_expansion    False -> 1 step    (a.yaml capacity_expansion=false; single
                                            investment over the horizon). TOGGLEABLE.
  n_res_sources         1                  (Solar PV)
  generator             disabled           (system_configuration=1 => battery only;
                                            template default max_installable_kw=0)

Unit bridges (old used W-based costs; new uses kW/kWh):
  PV   res_specific_investment_cost 0.95 €/W  -> 950 €/kW
  BESS battery_specific_investment_cost 0.65 €/Wh -> 650 €/kWh

NOTE: a few old parameters have no clean 1:1 mapping (e.g. absolute O&M cost vs the
new fixed_om_share_of_capex, battery cycle life vs calendar lifetime). These are set
to defensible values and flagged with `# THESIS-MAP` so they can be tuned to match
the thesis exactly before the production run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from mgpy2.paths import ensure_engines_importable, projects_root
from mgpy2.input_prep import PrepConfig, ResourceParams, year_labels, write_demand_and_resource


# =============================================================================
# Global (per-run) techno-economic assumptions — mapped from a.yaml
# =============================================================================
@dataclass
class ThesisConfig:
    # structural
    formulation: str = "dynamic"
    system_type: str = "off_grid"
    time_horizon_years: int = 20
    start_year: int = 2025
    social_discount_rate: float = 0.10
    capacity_expansion: bool = False        # single investment step over the horizon
    demand_growth: float = 0.03
    demand_growth_mode: str = "thesis_faithful"   # see input_prep.PrepConfig

    # renewable (Solar PV) — costs already converted to per-kW
    res_resource_label: str = "Solar"
    res_conversion_label: str = "Solar"
    pv_specific_investment_cost_per_kw: float = 950.0   # 0.95 €/W  # THESIS-MAP
    pv_wacc: float = 0.10                                            # THESIS-MAP (uses discount rate)
    pv_lifetime_years: int = 20
    pv_fixed_om_share_per_year: float = 0.0105          # ~0.01 €/W on 0.95 €/W  # THESIS-MAP
    pv_max_installable_capacity_kw: float = 1e6
    pv_inverter_specific_investment_cost_per_kw_ac: float = 200.0   # 0.2 €/W  # THESIS-MAP
    pv_inverter_lifetime_years: int = 20
    pv_dc_ac_ratio: float = 1.0
    pv_inverter_efficiency: float = 0.95

    # battery — costs already converted to per-kWh
    battery_label: str = "Battery"
    battery_specific_investment_cost_per_kwh: float = 650.0   # 0.65 €/Wh  # THESIS-MAP
    battery_wacc: float = 0.10                                             # THESIS-MAP
    battery_calendar_lifetime_years: int = 8                  # battery_expected_lifetime
    battery_charge_efficiency: float = 0.9
    battery_discharge_efficiency: float = 0.9
    battery_depth_of_discharge: float = 0.8
    battery_initial_soc: float = 1.0
    battery_fixed_om_share_per_year: float = 0.0385          # 0.025 €/Wh on 0.65  # THESIS-MAP
    battery_max_installable_capacity_kwh: float = 1e6
    battery_inverter_specific_investment_cost_per_kw: float = 300.0  # 0.3 €/W  # THESIS-MAP
    battery_inverter_lifetime_years: int = 8
    # Battery power/energy ratio (c-rate, 1/h). CRITICAL: the new engine treats a
    # null/omitted c-rate as 0, which forces the battery inverter power to zero and
    # silently disables the battery (=> PV-only, infeasible off-grid at night).
    # The thesis sets these via charge/discharge TIMES (a.yaml maximum_battery_charge_time
    # = 5 h -> 0.2/h; maximum_battery_discharge_time = 4 h -> 0.25/h).
    battery_max_charge_c_rate: float = 1.0 / 5.0     # THESIS-MAP: 1 / max charge time (5 h)
    battery_max_discharge_c_rate: float = 1.0 / 4.0  # THESIS-MAP: 1 / max discharge time (4 h)

    # --- optional system extensions (NOT required for feasibility) --------------
    # A pure PV+battery, off-grid, zero-lost-load system (the thesis design) is
    # feasible once the battery c-rates above are set. These remain available if you
    # want to add a diesel backup or allow unserved energy for sensitivity studies.
    include_generator: bool = False
    gen_specific_investment_cost_per_kw: float = 500.0   # 0.5 €/W
    gen_wacc: float = 0.10
    gen_lifetime_years: int = 20
    gen_nominal_efficiency: float = 0.30
    gen_fuel_lhv_kwh_per_unit: float = 10.14             # a.yaml fuel_lhv 10140 Wh -> kWh
    gen_fuel_cost_per_unit: float = 1.0                  # THESIS-MAP (fuel price)
    gen_max_installable_capacity_kw: float = 1e6

    max_lost_load_fraction: float = 0.0
    lost_load_cost_per_kwh: float = 0.0

    # solver / misc
    csv_delimiter: str = ","
    csv_decimal: str = "."

    # HPC results-export policy
    export_profile: str = "core"      # "core" = lean bundle (summary.json + dispatch); "full" = legacy CSV+Excel bundle
    dispatch_format: str = "parquet"  # "parquet" (small/fast) or "csv" (comfortable for manual inspection)

    # PVGIS panel params (a.yaml resource_assessment)
    pv_nom_power_w: float = 1000.0
    pv_tilt: float = 10.0
    pv_azimuth: float = 180.0

    def horizon(self) -> int:
        return int(self.time_horizon_years)

    def year_labels(self) -> List[str]:
        return year_labels(self.start_year, self.horizon())

    def investment_steps_years(self) -> Optional[List[int]]:
        # capacity_expansion False -> a single step spanning the whole horizon
        return None if not self.capacity_expansion else [self.horizon()]


# =============================================================================
# formulation.json (mirror of pages/0_Project_Setup.write_formulation_file)
# =============================================================================
def build_formulation_payload(project_name: str, cfg: ThesisConfig, description: str = "") -> Dict[str, Any]:
    from datetime import datetime
    steps = cfg.investment_steps_years()
    return {
        "project_name": project_name,
        "description": description,
        "module": "generation_planning",
        "created_at": datetime.now().isoformat() + "Z",
        "core_formulation": cfg.formulation,
        "system_type": cfg.system_type,
        "on_grid": cfg.system_type == "on_grid",
        "grid_allow_export": False,
        "unit_commitment": False,
        "start_year_label": str(cfg.start_year),
        "time_horizon_years": cfg.horizon(),
        "social_discount_rate": cfg.social_discount_rate,
        "capacity_expansion": cfg.capacity_expansion,
        "investment_steps_years": steps,
        "multi_scenario": {"enabled": False, "n_scenarios": 1,
                            "scenario_labels": ["scenario_1"], "scenario_weights": [1.0]},
        "optimization_constraints": {
            "enforcement": "scenario_wise",
            "min_renewable_penetration": 0.0,
            "max_lost_load_fraction": cfg.max_lost_load_fraction,
            "lost_load_cost_per_kwh": cfg.lost_load_cost_per_kwh,
            "land_availability_m2": 1e12,
            "emission_cost_per_kgco2e": 0.0,
        },
        "system_configuration": {"n_sources": 1},
        "battery_model": {"loss_model": "constant_efficiency",
                          "degradation_model": {"cycle_fade_enabled": False, "calendar_fade_enabled": False}},
        "generator_model": {"efficiency_model": "constant_efficiency"},
        "csv_format": {"delimiter": cfg.csv_delimiter, "decimal": cfg.csv_decimal},
    }


def build_template_settings(cfg: ThesisConfig):
    ensure_engines_importable()
    from core.io.templates import TemplateSettings
    return TemplateSettings(
        formulation=cfg.formulation,
        system_type=cfg.system_type,
        allow_export=False,
        multi_scenario=False,
        n_scenarios=1,
        scenario_labels=["scenario_1"],
        scenario_weights=[1.0],
        start_year_label=str(cfg.start_year),
        horizon_years=cfg.horizon(),
        capacity_expansion=cfg.capacity_expansion,
        investment_steps_years=cfg.investment_steps_years(),
        n_res_sources=1,
        conversion_labels=[cfg.res_conversion_label],
        resource_labels=[cfg.res_resource_label],
        battery_label=cfg.battery_label,
        battery_loss_model="constant_efficiency",
        battery_cycle_fade_enabled=False,
        battery_calendar_fade_enabled=False,
        battery_efficiency_curve_csv="",
        battery_cycle_lifetime_to_eol_cycles=3000.0,
        battery_calendar_fade_curve_csv="",
        battery_calendar_time_increment_per_step=0.0,
        battery_end_of_life_soh=0.8,
        generator_label="Generator",
        generator_efficiency_model="constant_efficiency",
        generator_efficiency_curve_csv="",
        fuel_label="Fuel",
        csv_delimiter=cfg.csv_delimiter,
        csv_decimal=cfg.csv_decimal,
    )


# =============================================================================
# YAML patching (apply thesis values to every investment step)
# =============================================================================
def _patch_all_steps(by_step: Dict[str, Any], updates: Dict[str, Any]) -> None:
    for step_key, block in by_step.items():
        if isinstance(block, dict):
            block.update(updates)


def _patch_renewables_yaml(path: Path, cfg: ThesisConfig) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for res in doc.get("renewables", []):
        res["conversion_technology"] = cfg.res_conversion_label
        res["resource"] = cfg.res_resource_label
        by_step = res.get("investment", {}).get("by_step", {})
        _patch_all_steps(by_step, {
            "nominal_capacity_kw": 1.0,
            "specific_investment_cost_per_kw": cfg.pv_specific_investment_cost_per_kw,
            "wacc": cfg.pv_wacc,
            "lifetime_years": cfg.pv_lifetime_years,
            "fixed_om_share_per_year": cfg.pv_fixed_om_share_per_year,
            "inverter_specific_investment_cost_per_kw_ac": cfg.pv_inverter_specific_investment_cost_per_kw_ac,
            "inverter_lifetime_years": cfg.pv_inverter_lifetime_years,
            "grant_share_of_capex": 0.0,
            "production_subsidy_per_kwh": 0.0,
        })
        tech = res.setdefault("technical", {})
        tech.update({
            "dc_ac_ratio": cfg.pv_dc_ac_ratio,
            "inverter_efficiency": cfg.pv_inverter_efficiency,
            "max_installable_capacity_kw": cfg.pv_max_installable_capacity_kw,
            "capacity_degradation_rate_per_year": 0.0,
        })
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _patch_battery_yaml(path: Path, cfg: ThesisConfig) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    bat = doc.get("battery", {})
    by_step = bat.get("investment", {}).get("by_step", {})
    _patch_all_steps(by_step, {
        "nominal_capacity_kwh": 1.0,
        "specific_investment_cost_per_kwh": cfg.battery_specific_investment_cost_per_kwh,
        "wacc": cfg.battery_wacc,
        "calendar_lifetime_years": cfg.battery_calendar_lifetime_years,
        "fixed_om_share_per_year": cfg.battery_fixed_om_share_per_year,
        "inverter_specific_investment_cost_per_kw": cfg.battery_inverter_specific_investment_cost_per_kw,
        "inverter_lifetime_years": cfg.battery_inverter_lifetime_years,
        "grant_share_of_capex": 0.0,
    })
    tech = bat.setdefault("technical", {})
    tech.update({
        "charge_efficiency": cfg.battery_charge_efficiency,
        "discharge_efficiency": cfg.battery_discharge_efficiency,
        "depth_of_discharge": cfg.battery_depth_of_discharge,
        "initial_soc": cfg.battery_initial_soc,
        "max_installable_capacity_kwh": cfg.battery_max_installable_capacity_kwh,
        # MUST be finite/positive — null is read as 0 and disables the battery.
        "max_charge_c_rate": cfg.battery_max_charge_c_rate,
        "max_discharge_c_rate": cfg.battery_max_discharge_c_rate,
    })
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _patch_generator_yaml(path: Path, cfg: ThesisConfig) -> None:
    """Disable the generator (thesis default) or enable a diesel backup + fuel."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    gen = doc.get("generator", {})
    tech = gen.setdefault("technical", {})
    if not cfg.include_generator:
        tech["max_installable_capacity_kw"] = 0  # hard-disable -> PV+battery only
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return
    tech["max_installable_capacity_kw"] = cfg.gen_max_installable_capacity_kw
    tech["nominal_efficiency_full_load"] = cfg.gen_nominal_efficiency
    _patch_all_steps(gen.get("investment", {}).get("by_step", {}), {
        "nominal_capacity_kw": 1.0,
        "specific_investment_cost_per_kw": cfg.gen_specific_investment_cost_per_kw,
        "wacc": cfg.gen_wacc,
        "lifetime_years": cfg.gen_lifetime_years,
        "fixed_om_share_per_year": 0.0,
    })
    fuel = doc.setdefault("fuel", {})
    fuel.setdefault("technical", {})["lhv_kwh_per_unit_fuel"] = cfg.gen_fuel_lhv_kwh_per_unit
    for sc in fuel.get("cost", {}).get("by_scenario", {}).values():
        n = len(sc.get("by_year_cost_per_unit_fuel", [])) or cfg.horizon()
        sc["by_year_cost_per_unit_fuel"] = [cfg.gen_fuel_cost_per_unit] * n
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


# =============================================================================
# Top-level: build a project ready to solve
# =============================================================================
def build_project(project_name: str, row: dict, cfg: ThesisConfig,
                  base_dir: Optional[Path] = None, overwrite: bool = True) -> Path:
    """Scaffold projects/<project_name>/inputs with all files + the demand/resource CSVs.
    Returns the project root Path."""
    ensure_engines_importable()
    from core.io.utils import ensure_project_structure, project_paths
    from core.io.jsonio import write_json
    from core.io.templates import write_templates

    paths = ensure_project_structure(project_name, base_dir=base_dir)

    # 1) formulation.json
    write_json(paths.formulation_json, build_formulation_payload(project_name, cfg))

    # 2) structurally-correct templates (yaml + zero-filled CSVs)
    write_templates(paths, build_template_settings(cfg), overwrite=overwrite)

    # 3) patch techno-economic values
    _patch_renewables_yaml(paths.inputs_dir / "renewables.yaml", cfg)
    _patch_battery_yaml(paths.inputs_dir / "battery.yaml", cfg)
    _patch_generator_yaml(paths.inputs_dir / "generator.yaml", cfg)

    # 4) demand + solar (overwrite the zero-filled CSV templates)
    prep = PrepConfig(
        years=cfg.horizon(), year_labels=cfg.year_labels(),
        demand_growth=cfg.demand_growth, demand_growth_mode=cfg.demand_growth_mode,
        resource_labels=[cfg.res_resource_label],
        csv_sep=cfg.csv_delimiter, csv_decimal=cfg.csv_decimal,
    )
    res = ResourceParams(
        lat=float(row["lat"]), lon=float(row["lon"]),
        nom_power=cfg.pv_nom_power_w, tilt=cfg.pv_tilt, azim=cfg.pv_azimuth,
    )
    write_demand_and_resource(paths.inputs_dir, row, res, prep)
    return paths.root
