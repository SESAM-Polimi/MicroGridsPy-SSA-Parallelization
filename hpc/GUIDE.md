# HPC guide — launching a whole country

End-to-end runbook for solving every cluster of one country on the SGE HPC using
the `mgpy2` pipeline (new MicroGridsPy engine). One SGE array task = one cluster.

---

## 0. One-time setup on the HPC

```bash
# 1. get the code with git (the cluster can reach GitHub); work in scratch
cd /global-scratch/flash_pool/$USER
git clone -b development https://github.com/SESAM-Polimi/MicroGridsPy-SSA-Parallelization.git
cd MicroGridsPy-SSA-Parallelization
# later updates:  git pull

# 2. conda env that imports BOTH engines (see hpc/requirements-cluster.txt)
conda activate mgpy_clean

# 3. sanity check: both engines + the pipeline import
python -c "import sys; sys.path[:0]=['engines/new','engines/old']; \
import core.multi_year_model.model, microgridspy.utils.archetypes, mgpy2.run_cluster; print('OK')"
```

## 1. Edit the ONE config file: `hpc/env.sh`

All settings live there; each one can also be overridden for a single command,
e.g. `SAMPLE_CSV=... ./hpc/submit_jobs.sh`. The main ones:

```bash
CONDA_BASE="$HOME/miniconda3"     # conda install
CONDA_ENV="mgpy_clean"
SAMPLE_CSV="data/sample_input_2025/ETH/advanced_sample.csv"   # WHICH COUNTRY (see §4)
SGE_QUEUE="energia.q"
SGE_NODES="node-1-3"              # only nodes you are allowed to use
H_VMEM="8G"
H_RT="06:00:00"
MAX_CONCURRENT="16"
SOLVER="gurobi"                   # passed explicitly to every task
FEASIBILITY_FLAGS=""              # empty = thesis PV+battery design (see §2)
RUN_MODE="full"                   # "full" (nodes have internet) or "solve-only" (see §3)
EXPORT_PROFILE="core"             # lean per-cluster output (summary.json + dispatch)
```

## 2. System options (optional)

The thesis design — PV+battery, off-grid, zero lost-load — is feasible out of the box
(the battery c-rates are set correctly in `config_map.py`). For a faithful
reproduction leave `FEASIBILITY_FLAGS=""` in `hpc/env.sh`.

Only for sensitivity studies you may add:
- `FEASIBILITY_FLAGS="--include-generator"` — add a diesel backup, or
- `FEASIBILITY_FLAGS="--max-lost-load 0.05 --lost-load-cost 5.0"` — allow unserved energy.

> These options are applied at PREPARE time (written into each project's config), so if
> you use them, set them on the array task in `full` mode or on the prefetch step in
> `solve-only` mode (§3).

## 3. Internet on compute nodes? (PVGIS)

Each cluster downloads solar data from PVGIS over the internet.

- **Compute nodes HAVE internet** → keep `RUN_MODE="full"`; each task prepares + solves.
- **Compute nodes are OFFLINE** (common) → pre-stage inputs on the login node, then set
  `RUN_MODE="solve-only"`:

```bash
# on the LOGIN node (has internet) — parallel PVGIS/demand prefetch for the whole country
python orchestrator.py --csv "$SAMPLE_CSV" --prepare-only --workers 8
# then set RUN_MODE="solve-only" in hpc/env.sh and submit (§5)
```

## 4. Choose the country sample

Every script reads the sample given by **`SAMPLE_CSV`** (set in `hpc/env.sh`, or
`--csv` / `--sample` on the Python tools). Do NOT copy files to the repo root: that
old convention is gone. Task *N* always means row *N* of the sample as read by
`mgpy2/sample.py` (`//` comment rows skipped); every script uses that one mapping.

```bash
python -m mgpy2.sample "$SAMPLE_CSV"      # prints the number of tasks
```
(Only regenerate from raw GIS with `sample_generation/` if you must — those scripts
still contain hardcoded `/Users/matteo/...` paths and need editing first.)

## 5. Submit the whole country

```bash
./hpc/submit_jobs.sh        # counts rows, prints the plan (check "Sample:"!), submits the array
```

## 6. Monitor, recover, aggregate

```bash
./hpc/monitor_jobs.sh                       # progress + queue + clusters with error.txt

# rerun only failed/incomplete clusters (marker = results/summary.json)
source hpc/env.sh && activate_env
python hpc/make_failed_task_list.py
M=$(wc -l < tasks_failed.txt)
qsub -q "$SGE_QUEUE" -l h_vmem="$H_VMEM" -l h_rt="$H_RT" \
     -l hostname="$SGE_NODES" -v SAMPLE_CSV="$SAMPLE_CSV" \
     -t 1-"$M" -tc "$MAX_CONCURRENT" hpc/submit_rerun_array.sh

# aggregate all results into an enriched CSV (+ optional map)
python -m mgpy2.postprocess --out results_ETH.csv --gpkg results_ETH.gpkg
```

Per-cluster outputs land in `projects/<cat>/results/` (`summary.json`: LCOE, NPC,
investment, sizing; `dispatch.parquet`); the full result set is rebuilt offline with
`python -m mgpy2.reporting`. Logs in `logs/`.

---

## Hardcoded paths — status

| Location | Path | Action |
|---|---|---|
| `mgpy2/` | none | ✅ all via `paths.py` (+ `MGPY2_*` env overrides) |
| `hpc/env.sh` | `CONDA_BASE`, queue, nodes, `SAMPLE_CSV` | **edit once** for your account/site/country |
| `sample_generation/*.py` | `/Users/matteo/...` | only if regenerating samples; otherwise ignore |

## What was improved vs the original .sh

1. **Fixed a silent SGE bug:** resource directives (`-l h_vmem/h_rt/hostname`) were
   placed *after* shell commands in the old array script, so SGE ignored them. They
   are now passed on the `qsub` command line and always honoured.
2. **Removed the buried hardcoded conda path** → single `hpc/env.sh` (with `$HOME` default).
3. **Fixed the real infeasibility root cause:** the battery charge/discharge c-rates
   were left null, which the new engine reads as 0 → battery inverter power forced to 0
   → battery disabled → PV-only, infeasible at night. `config_map.py` now sets them from
   the thesis charge/discharge times (5 h → 0.2, 4 h → 0.25), so PV+battery works with
   no generator or lost-load needed.
4. **prepare/solve split** for offline compute nodes (PVGIS prefetch on the login node).
5. Added `set -euo pipefail`, clearer logging, and repo resolution via `$SGE_O_WORKDIR`
   in the array scripts (`$0` points to SGE's spool copy there).
6. **One sample setting, one task mapping:** `SAMPLE_CSV` + `mgpy2/sample.py`, used by
   every script (previously four separate row-counting implementations).
