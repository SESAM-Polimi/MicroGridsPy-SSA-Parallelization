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
- **Added temperature-driven battery degradation** (Li-ion only):
  `data_pipeline/battery_degradation_coefficients.py` + `layer1_liion_coefficients.json` (copied
  verbatim from upstream MicroGridsPy `b76a32d`, and used for the CYCLE-fade shape only). New
  `Params.battery_cycle_fade_coefficient`, `battery_calendar_rate_per_year`, `battery_end_of_life_soh`,
  `battery_cycle_lifetime_to_eol_cycles`; new `degradation_model.coefficients_enabled` (independent of
  the older flat `cycle_fade_enabled` scheme).
- **Diverged from upstream on the lifetime formulation.** Upstream resolves cycle fade over
  `n_soc_bands` SOC bands and charges it through a `battery_replacement_cost` epigraph
  `Z >= max(calendar annuity, c_repl * phi * F_cyc)`, with calendar fade entering only as a declining
  availability ceiling. Here: no bands (the depth non-linearity is 1.7 % and cost +1.6M LP variables),
  no epigraph (its cycle branch never binds at SSA temperatures and realistic cycling, so the wear
  charge was identically zero), calendar and cycle fade add in the capacity state instead of taking a
  min, and a new `battery_effective_energy_capacity_end_of_life` floor makes the rated cycle life
  binding. See `docs/PIPELINE.md` §4c.
- **Battery ageing works with constant efficiency** (`multi_year_model/variables.py`,
  `constraints.py`). The coefficient-based degradation layer no longer requires
  `loss_model='convex_loss_epigraph'`: the SoC balance, year links and cycle-fade throughput are
  written on DC-side flows that are either the epigraph variables or the expressions
  `eta_c * P_ch` and `P_dis / eta_d`. The SSA pipeline default is now constant efficiency (the
  epigraph made Gurobi 10-20x slower). The legacy `cycle_fade_enabled`/`calendar_fade_enabled`
  scheme still requires the epigraph. See `docs/BATTERY_DEGRADATION.md` §4.3.
- **Solver termination condition recorded** (`multi_year_model/model.py`): the solution's attrs
  now carry `termination_condition` ("optimal", "suboptimal", "time_limit", ...) next to linopy's
  coarse `status`, so the pipeline can refuse results that are not a proven optimum.
- **Replaced upstream's calendar-ageing coefficients.** Upstream evaluates a hard-coded per-hour
  cubic in ambient temperature (which also disagrees by about 6x with the `alpha_poly` in its own
  JSON) giving 0.05 %/yr at 25 degC, i.e. a 386-year calendar life. Here calendar fade uses the
  empirical Ali et al. (2023) storage fit, renormalised onto a measured 10-year LFP shelf test, and
  is evaluated at cell temperature (ambient + a new `enclosure_temperature_rise_c`) against a mean
  SoC of `1 - DoD/2` rather than a step on DoD. See `docs/PIPELINE.md` §4b.
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
