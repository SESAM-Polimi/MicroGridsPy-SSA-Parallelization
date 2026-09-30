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
- **Generator part-load removed from the multi-year model.** Fuel use is the constant-efficiency
  relation `gen == fuel * LHV * nominal_efficiency_full_load` (constraint
  `fuel_to_power_nominal_eta`). The segmented `fuel_to_power_partial_load_*` constraints, the
  `Params.generator_*curve*` fields and the curve loading in `multi_year_model/data.py` are gone;
  a non-empty `generator.technical.efficiency_curve_csv` raises `InputValidationError`.
  (`data_pipeline/generator_partial_load_model.py` and the typical-year code are untouched.)
- **Added ambient temperature** to the multi-year model: optional `inputs/ambient_temperature.csv`
  (`_load_ambient_temperature_csv` in `multi_year_model/data.py`), exposed as
  `Params.ambient_temperature` (period, year, scenario), transposed in
  `data_pipeline/multi_year_loader._align_contract_dims`.
- **Added battery-degradation coefficient inputs** (Li-ion only, no LP use yet):
  `data_pipeline/battery_degradation_coefficients.py` + `layer1_liion_coefficients.json` (copied
  verbatim from upstream MicroGridsPy `b76a32d`; calendar alpha polynomials are the ones hard-coded in
  upstream's `.py`, not the JSON's own `alpha_poly`). New `Params.battery_ck_bands`,
  `battery_calendar_rate_per_year`, `battery_end_of_life_soh`, `battery_cycle_lifetime_to_eol_cycles`;
  new `degradation_model.coefficients_enabled` / `n_soc_bands` (independent of the older flat
  `cycle_fade_enabled` scheme).
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
