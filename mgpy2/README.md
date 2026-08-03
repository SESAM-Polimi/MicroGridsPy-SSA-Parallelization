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
   -> core.export.multi_year_results.export_multi_year_results       -> projects/<cat>/results/*
postprocess.aggregate  -> enriched sample CSV (+ optional .gpkg map)
```
Completion marker: `projects/<cat>/results/reporting_summary.csv` (LCOE lives here).

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

## TWO OPEN DECISIONS (flagged, not silently resolved)

1. **Feasibility of PV+battery-only.** The thesis system (PV+battery, no generator,
   0 lost load) is INFEASIBLE under the new engine's formulation, though it solved
   in the old one. Choose a feasibility option on `ThesisConfig` / CLI:
   - `--include-generator` (adds a diesel backup — changes the system design), or
   - `--max-lost-load 0.05 --lost-load-cost <c>` (allows unserved energy — changes LCOE).
   Default is the faithful-but-infeasible PV+battery-only; pick one before a real run.

2. **Demand-growth quirk.** The old `apply_demand_growth` divides by 100, so YAML
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
