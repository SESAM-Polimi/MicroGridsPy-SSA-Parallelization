# MicroGridsPy SSA Parallelization

Large-scale, parallel least-cost sizing of solar+battery(+backup) mini-grids for
thousands of population clusters across Sub-Saharan Africa, on the **updated
MicroGridsPy engine**. One CSV row = one cluster = one independent optimization,
run in parallel locally or on an SGE HPC.

## Layout

```
.
├── mgpy2/                 # the pipeline package (see mgpy2/README.md)
│   ├── input_prep.py      #   old archetypes+PVGIS  -> new-format demand/resource CSVs
│   ├── config_map.py      #   a.yaml + sample row   -> formulation.json + split YAMLs
│   ├── run_cluster.py     #   build + solve one cluster on the new engine
│   ├── postprocess.py     #   aggregate results (LCOE, sizing) back into the sample
│   └── paths.py           #   repo-relative, env-overridable paths
├── orchestrator.py        # LOCAL parallel runner (ProcessPool)
├── hpc/                   # SGE job-array scripts (submit / rerun / monitor)
├── sample_generation/     # build advanced_sample.csv (GIS; unchanged from thesis)
├── engines/
│   ├── new/               # MicroGridsPy Updated (package `core`) — the solver
│   └── old/               # MicroGridsPy-Development_Linopy-2 (package `microgridspy`)
│                          #   — REQUIRED: provides archetype demand + PVGIS code
├── data/
│   ├── data_sheet/        # School_weights.csv, hdi_values.csv, a.yaml (reference), ...
│   ├── sample_input_2025/ # per-country inputs
│   └── thesis_results_2026/  # thesis output samples (validation reference)
└── projects/              # per-cluster inputs+results (runtime output)
```

## Quick start

Environment: a conda env that imports **both** engines (verified: `mgpy_planning`,
py3.11, with linopy + highspy). HiGHS is the default solver (no license needed).

```bash
# one cluster
python -m mgpy2.run_cluster --cat GHSL_1 --solver highs

# whole sample, locally, 3 workers
python orchestrator.py --csv advanced_sample.csv --workers 3 --horizon 20

# aggregate results
python -m mgpy2.postprocess --sample advanced_sample.csv
```

HPC (submit from repo root): `./hpc/submit_jobs.sh`, monitor with `./hpc/monitor_jobs.sh`.

## One decision before a production run

- **Demand growth** — the old code grew households at `demand_growth/100` (a quirk);
   `mgpy2` reproduces this faithfully by default (`demand_growth_mode`), with a
   `consistent` option. Year-1 demand matches the thesis to the decimal.

See [mgpy2/README.md](mgpy2/README.md) for details, the a.yaml→new-config mapping, and
the `# THESIS-MAP` techno-economic values to tune before the full run.
```
