# Vendored engine: MicroGridsPy (Updated) — `core.*`

This directory is a vendored copy of the updated MicroGridsPy engine, used here
purely as the **solver + results core** for the SSA cluster pipeline. The cluster
is compute-only; all post-processing and visualization happen offline.

## Local modifications vs. upstream

- **Added** `core/export/multi_year_core.py` — lean cluster export
  (`export_core_outputs`: writes only `summary.json` + `dispatch.<parquet|csv>`).
- **Added** a `profile="core"|"full"` (+ `dispatch_format`) toggle to
  `core/export/multi_year_results.export_multi_year_results` (default `full` is
  unchanged; `core` delegates to `multi_year_core`).
- **Removed the Streamlit GUI** (not needed on the cluster; offline analysis uses
  `mgpy2.reporting`):
  - `Home.py`, `pages/`, `assets/`
  - `core/visualization/` (was imported only by `pages/`)
  - `core/export/plots.py` (matplotlib; had no importers)
  Kept: `core/export/results_page_helpers.py` and `core/export/typical_year_*`
  (imported by the test suite; no GUI dependencies).

To restore the GUI or re-sync with upstream, re-pull those paths from the upstream
MicroGridsPy Updated repository at the pinned revision recorded in the pipeline's
top-level README / git history.
