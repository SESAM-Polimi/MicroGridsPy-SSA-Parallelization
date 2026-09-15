# mgpy2 — SSA parallelization on the NEW MicroGridsPy engine

`mgpy2` re-implements the thesis cluster-parallelization workflow on the updated
engine (`engines/new/`, package `core.*`), reusing the OLD engine's
(`engines/old/`, package `microgridspy.*`) archetype demand and PVGIS solar code
as a preprocessing library.

## Pipeline (per cluster)
```
advanced_sample.csv row
   -> config_map.build_project        # formulation.json + split YAMLs (write_templates + patched values)
   -> input_prep.write_demand_and_resource
        demand:   microgridspy.utils.archetypes.demand_calculation  (+ School_weights)  -> load_demand.csv (kWh)
        solar:    microgridspy.utils.pvgis.download_pvgis_pv_data    -> resource_availability.csv (capacity factor)
   -> run_cluster: MultiYearModel(project).solve_single_objective(highs)
   -> export (profile="core", default): summary.json + dispatch.<parquet|csv>
      (profile="full" writes the legacy CSV+Excel bundle)          -> projects/<cat>/results/
postprocess.aggregate  -> enriched sample CSV (+ optional .gpkg map)
```
Completion marker: `projects/<cat>/results/summary.json` (holds meta + metrics
[NPC, LCOE, investment] + sizing). The full per-year/-scenario result set
(energy_balance, kpis, cashflows, inverter metrics, reporting_summary, ...) is
rebuilt OFFLINE from `summary.json` + `dispatch` + inputs via `mgpy2.reporting`
(`python -m mgpy2.reporting --cat <id> [--out DIR]`).

## Environment
Use a conda env that imports BOTH engines (verified: `mgpy_planning`, py3.11, with
linopy + highspy). HiGHS is the default solver (no license).

## Run
Local (parallel):
```bash
python orchestrator.py --csv advanced_sample.csv --workers 3 --solver highs --horizon 20
```
One cluster (SGE-style):
```bash
python -m mgpy2.run_cluster --task-id 1 --solver highs
python -m mgpy2.run_cluster --cat GHSL_1 --solver highs
```
HPC array (submit from repo root): `./hpc/submit_jobs.sh` -> `hpc/submit_array.sh`;
rerun failures with `python hpc/make_failed_task_list.py` + `qsub -t 1-M hpc/submit_rerun_array.sh`;
watch with `./hpc/monitor_jobs.sh`. Aggregate with `python -m mgpy2.postprocess`.

Paths are repo-relative and overridable via env vars (see `paths.py`):
`MGPY2_NEW_ENGINE`, `MGPY2_OLD_ENGINE`, `MGPY2_PROJECTS_DIR`, `MGPY2_DATA_SHEET`.

## Battery c-rate (resolved — was the "infeasibility")

The thesis system (PV+battery, off-grid, 0 lost load, no generator) is feasible and is
the default. Earlier it appeared infeasible because the battery charge/discharge
**c-rates** were left null, and the new engine reads a null c-rate as **0**, which forces
the battery inverter power to zero and disables the battery. `config_map.ThesisConfig`
now sets them from the thesis charge/discharge times
(`battery_max_charge_c_rate = 1/5 = 0.2`, `battery_max_discharge_c_rate = 1/4 = 0.25`).
Optional `--include-generator` / `--max-lost-load` remain for sensitivity studies only.

## ONE OPEN DECISION

1. **Demand-growth quirk.** The old `apply_demand_growth` divides by 100, so YAML
   `demand_growth: 0.03` grew households/hospitals at 0.03%/yr while schools grew at
   3%/yr. `PrepConfig.demand_growth_mode` selects `"thesis_faithful"` (default,
   reproduces this exactly — Year-1 demand matches the thesis to the decimal) or
   `"consistent"` (single uniform (1+g)^y on all demand).

## Techno-economic mapping
`config_map.ThesisConfig` holds the a.yaml-derived values with unit bridges applied
(PV 0.95 €/W -> 950 €/kW; battery 0.65 €/Wh -> 650 €/kWh). Fields marked `# THESIS-MAP`
have no clean 1:1 mapping (e.g. absolute O&M vs fixed_om_share) and should be tuned to
match the thesis exactly before the production run.
```
