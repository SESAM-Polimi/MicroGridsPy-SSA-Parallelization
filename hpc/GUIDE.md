# HPC guide — launching a whole country

End-to-end runbook for solving every cluster of one country on the SGE HPC using
the `mgpy2` pipeline (new MicroGridsPy engine). One SGE array task = one cluster.

---

## 0. One-time setup on the HPC

```bash
# 1. copy the repo to the cluster (rsync; skip the big demo projects if you like)
rsync -av --exclude 'engines/new/projects' ./MicroGridsPy-SSA-Parallelization-main/ \
      user@hpc:~/mgpy_ssa/

# 2. create the conda env that imports BOTH engines
cd ~/mgpy_ssa
conda env create -f engines/new/environment.yml -n mgpy_planning   # or mamba
conda activate mgpy_planning

# 3. sanity check: both engines + the pipeline import
python -c "import sys; sys.path[:0]=['engines/new','engines/old']; \
import core.multi_year_model.model, microgridspy.utils.archetypes, mgpy2.run_cluster; print('OK')"
```

## 1. Edit the ONE config file: `hpc/env.sh`

The only path you MUST change is your conda base. Everything else is optional.

```bash
CONDA_BASE="$HOME/miniconda3"     # <-- your conda install (NOT the old teammate path)
CONDA_ENV="mgpy_planning"
SGE_QUEUE="energia.q"             # your queue
SGE_NODES="node-1-3|node-1-6|node-1-7|node-1-8"   # or "" for no node restriction
H_VMEM="8G"                       # 20-yr hourly model needs headroom (raise if OOM)
H_RT="06:00:00"
MAX_CONCURRENT="64"
FEASIBILITY_FLAGS="--include-generator"    # REQUIRED — see §2
RUN_MODE="full"                   # "full" (nodes have internet) or "solve-only" (see §3)
```

## 2. Choose a feasibility mode (REQUIRED — do not skip)

A pure PV+battery, off-grid, zero-lost-load system is **infeasible** in the new
engine, so a run with no feasibility flag makes **every cluster fail**. Pick one in
`hpc/env.sh`:

- `FEASIBILITY_FLAGS="--include-generator"` — add a diesel backup (changes system design), or
- `FEASIBILITY_FLAGS="--max-lost-load 0.05 --lost-load-cost 5.0"` — allow unserved energy.

> The feasibility choice is written into each project's config at PREPARE time, so it
> must be present whenever inputs are (re)generated — i.e. on the array task in `full`
> mode, or on the prefetch step in `solve-only` mode (§3).

## 3. Internet on compute nodes? (PVGIS)

Each cluster downloads solar data from PVGIS over the internet.

- **Compute nodes HAVE internet** → keep `RUN_MODE="full"`; each task prepares + solves.
- **Compute nodes are OFFLINE** (common) → pre-stage inputs on the login node, then set
  `RUN_MODE="solve-only"`:

```bash
# on the LOGIN node (has internet) — parallel PVGIS/demand prefetch for the whole country
python orchestrator.py --csv advanced_sample.csv --prepare-only --workers 8 --include-generator
# then set RUN_MODE="solve-only" in hpc/env.sh and submit (§5)
```

## 4. Stage the country sample

The pipeline reads `advanced_sample.csv` from the repo root. Ready-made per-country
samples already exist under `data/thesis_results_2026/`:

```bash
cp data/thesis_results_2026/GHA_sample.csv advanced_sample.csv   # e.g. Ghana
```
(Only regenerate from raw GIS with `sample_generation/` if you must — those scripts
still contain hardcoded `/Users/matteo/...` paths and need editing first.)

## 5. Submit the whole country

```bash
./hpc/submit_jobs.sh        # counts rows, prints the plan, submits the array
```

## 6. Monitor, recover, aggregate

```bash
./hpc/monitor_jobs.sh                       # progress + queue + clusters with error.txt

# rerun only failed/incomplete clusters (marker = results/reporting_summary.csv)
python hpc/make_failed_task_list.py
source hpc/env.sh
M=$(wc -l < tasks_failed.txt)
qsub -q "$SGE_QUEUE" -l h_vmem=$H_VMEM -l h_rt=$H_RT \
     $( [ -n "$SGE_NODES" ] && echo -l hostname=$SGE_NODES ) \
     -t 1-$M -tc $MAX_CONCURRENT hpc/submit_rerun_array.sh

# aggregate all results into an enriched CSV (+ optional map)
python -m mgpy2.postprocess --sample advanced_sample.csv \
       --out results_GHA.csv --gpkg results_GHA.gpkg
```

Per-cluster outputs land in `projects/<cat>/results/` (LCOE in `reporting_summary.csv`,
sizing in `capacity_by_year.csv` / `design_by_step.csv`); logs in `logs/`.

---

## Hardcoded paths — status

| Location | Path | Action |
|---|---|---|
| `mgpy2/` | none | ✅ all via `paths.py` (+ `MGPY2_*` env overrides) |
| `hpc/env.sh` | `CONDA_BASE`, queue, nodes | **edit once** for your account/site |
| `sample_generation/*.py` | `/Users/matteo/...` | only if regenerating samples; otherwise ignore |

## What was improved vs the original .sh

1. **Fixed a silent SGE bug:** resource directives (`-l h_vmem/h_rt/hostname`) were
   placed *after* shell commands in the old array script, so SGE ignored them. They
   are now passed on the `qsub` command line and always honoured.
2. **Removed the buried hardcoded conda path** → single `hpc/env.sh` (with `$HOME` default).
3. **Fixed a fatal default:** the old array ran with no feasibility flag → every job
   infeasible. Feasibility is now explicit and required.
4. **prepare/solve split** for offline compute nodes (PVGIS prefetch on the login node).
5. Added `set -uo pipefail`, cwd-independent repo resolution, and clearer logging.
