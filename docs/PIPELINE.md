# How the pipeline works

_Owner's guide to what happens between a row of the country sample and a solved mini-grid.
Written 16 Sep 2026 from the code on `main` and the thesis methodology
(Pieraccini, `02-Methodology.tex`). Where the two disagree, it is flagged **[CHECK]**._

---

## 1. The big picture

```
data/sample_input_2025/<ISO>/advanced_sample.csv      (one row = one cluster)
        │   SAMPLE_CSV (hpc/env.sh)  ─ task N = row N  (mgpy2/sample.py)
        ▼
SGE array task N  ── hpc/submit_array.sh ──►  python -m mgpy2.run_cluster --task-id N
        │
        ├─ 1. PREPARE  mgpy2/config_map.build_project
        │        ├─ project skeleton + techno-economic YAMLs   (ThesisConfig)
        │        ├─ demand     mgpy2/input_prep.compute_demand_kwh   ─► inputs/load_demand.csv
        │        ├─ solar      mgpy2/input_prep.compute_resource_and_temperature  ─► inputs/resource_availability.csv
        └─ temperature (same PVGIS call)                                  ─► inputs/ambient_temperature.csv
        │
        ├─ 2. SOLVE    engines/new  core.multi_year_model.MultiYearModel   (linopy + Gurobi/HiGHS)
        │
        └─ 3. EXPORT   core.export (profile "core")  ─► results/summary.json + dispatch.parquet
                        (full result set rebuilt offline: python -m mgpy2.reporting --cat <id>)

afterwards:  mgpy2/postprocess.py  ─► sample table enriched with sizing, NPC, LCOE
```

Everything per cluster lives in `projects/<cat>/` (`cat` = cluster id from the sample, e.g.
`GHSL_563`, `SCHOOL_7812`). Nothing in `projects/` is tracked by git.

**Two engines are involved.** `engines/new` is the optimisation model (MGPy2). `engines/old`
(MGPy1, the engine used in the thesis) is still imported, **only for demand and PVGIS**.
See §6.

---

## 2. The sample row

Each row carries, among others: `cat`, `lat`, `lon`, `cooling`, household counts per tier
(`h_tier1` … `h_tier5`), hospital counts per level (`hospital_1` … `hospital_5`), and
`school_total_demand` (annual school electricity use, Wh).

How the rows were built (thesis §Methods, not re-run by this code; `sample_generation/`):
- clusters from GHSL settlements + WorldPop, plus independent `SCHOOL_*` clusters from
  JRC/UNICEF SEADB (schools > 250 m from any settlement);
- households = population / national average household size, split into 5 tiers using the
  Relative Wealth Index distribution;
- health facilities from JRC, already classified by level;
- `cooling`: which part of the year needs cooling (all-year, none, Apr–Sep, Oct–Mar),
  derived from the climatic zone.

Rules the code relies on: `cat` must be unique (checked 15 Sep: 53,239 unique in ETH);
rows starting with `//` are comments and are skipped.

---

## 3. Demand: built per cluster, at run time, from fixed archetypes

`input_prep.compute_demand_kwh(row)` → old-engine `archetypes.demand_calculation(...)`.

1. **Zone from latitude** (`determine_zone`): F1 10–30°, F2 −10–10°, F3 −20–−10°,
   F4 −30–−20°, F5 < −30°. Outside −30…30° it raises an error.
2. **Households:** for each tier with a non-zero count, read
   `engines/old/microgridspy/utils/demand_archetypes/<cooling>_<zone>_Tier-<n>.xlsx`
   (cooling ∈ AS, AY, NC, OM) and add `count / 100 × profile`.
3. **Hospitals:** `HOSPITAL_Tier-<n>.xlsx`, added as `count × profile` (no /100).
4. **Schools:** NOT from `SCHOOL.xlsx`. The annual `school_total_demand` is spread over the
   hours with `data/data_sheet/School_weights.csv` (8760 weights).
5. **20 years:** the year-1 profile is repeated and grown (see §3.1).
6. Wh → kWh, written as `inputs/load_demand.csv` (8760 rows × 20 year columns).

What this means:
- **The archetypes are fixed profiles built with RAMP** (N. Stevanato et al., *Archetypes of
  Rural Users in Sub-Saharan Africa for Load Demand Estimation*). RAMP ran once, when the
  archetypes were created; no RAMP runs happen here. This is what the thesis means by
  "demand generated using RAMP". Clusters with the same zone, cooling type and tier mix
  therefore have **identical hourly shapes**, differing only in scale.
- **Household archetypes describe 100 households** (hence `count / 100`); hospital
  archetypes describe one facility (hence `count × profile`). Confirmed by the archetypes' author.
- **Zone boundaries are sharp:** neighbouring clusters either side of a zone limit
  (e.g. 10°N, crossing Ethiopia) get different profiles. This is by design of the archetypes.
- A missing `cooling` value defaults to `NC` (no cooling). Accepted behaviour.

### 3.1 Demand growth: the known quirk

`ThesisConfig.demand_growth = 0.03`, `demand_growth_mode = "thesis_faithful"` (default).

| Load | Growth actually applied | Why |
|---|---|---|
| households, hospitals | **0.03 % / year** | old `apply_demand_growth` divides by 100 |
| schools | **3 % / year** | `input_prep` applies `(1 + g)^y` |

The thesis text says **3 % for all clusters**. `demand_growth_mode = "consistent"` applies
one uniform `(1 + g)^y` to everything.
**Decision (15 Sep 2026):** keep `thesis_faithful` for the Ethiopia re-run, to isolate the
MGPy1 → MGPy2 engine change. Switch to `consistent` for the paper runs.
Every result records the mode it used: `summary.json` → `meta.run.pipeline_config.demand_growth_mode`.

---

## 4. Solar resource

`input_prep.compute_resource_and_temperature` → old-engine `pvgis.download_pvgis_pv_data`.

- Downloads the PVGIS **typical meteorological year** for the cluster's lat/lon
  (`https://re.jrc.ec.europa.eu/api/tmy`), tilt 10°, azimuth 180°, with a temperature correction.
- PV output for a 1000 W array ÷ 1000 → hourly **capacity factor**; the same year is
  repeated for all 20 years → `inputs/resource_availability.csv`.
- The same response carries the hourly 2 m air temperature (`T2m`, °C). It is tiled over the
  20 years the same way and written to `inputs/ambient_temperature.csv` (same layout as
  `load_demand.csv`). The engine loads it as `Params.ambient_temperature`
  (dims period × year × scenario); it is not used by the LP yet — it is there for the
  battery-degradation model. The file is optional, so older projects still load.
- **Needs internet on the node** in `RUN_MODE=full`. With offline nodes: prepare on the
  login node (`orchestrator.py --prepare-only`), then run with `RUN_MODE=solve-only`.
- **Fails loudly** (since 16 Sep 2026): each download has a timeout (10 s connect,
  120 s read) and up to 3 attempts (waits 30 s, 60 s). If all fail, or PVGIS returns no
  sun at all, the task ends with `results/error.txt` and appears on the rerun list.
  (Before, a failure silently became ZERO sun. In the PV+battery baseline such clusters
  were infeasible, so no August result is affected.)
- PVGIS updates its database over time, so re-downloading later may not give identical inputs.

### 4b. Battery degradation (Li-ion, temperature-driven)

`inputs/battery.yaml` carries `chemistry` (LFP | NMC), `initial_soh`, `end_of_life_soh`,
`cycle_lifetime_to_eol_cycles`; `formulation.json` → `battery_model.degradation_model` carries
`coefficients_enabled`. When enabled, the engine
(`data_pipeline/battery_degradation_coefficients.py`, pre-fitted `layer1_liion_coefficients.json`)
builds, from `ambient_temperature`:

- `Params.battery_cycle_fade_coefficient` (period × year × scenario): marginal cycle-fade cost
  `c(T) = Psi(DoD, T) / DoD`, fraction of nameplate lost per unit depth-fraction, so `c` times kWh
  of DC discharge gives kWh of capacity lost. Varies with T by 4.3x over 10-45 degC. Depth is not
  resolved: the reference `Psi(D)` is within 1.7 % of linear in `D` and flat past `D = 0.5`, so
  per-band LP states changed the optimum by 0.007 % and were dropped.

  **Magnitude** is pinned to the datasheet cycle life by solving
  `N_rated * Psi(cycle_life_reference_dod, cycle_life_reference_temperature_c) = initial_soh -
  end_of_life_soh`, so the rated cycle count is delivered exactly at the conditions it was quoted
  at. The shipped `Psi` shape was fitted at FULL DoD and 25 degC (layer1 meta: `test_DOD` 1.0,
  `test_T_C` 25, `N_cycles` 6000 to 80 % SoH), so upstream's `N_ref / N_user` scaling silently
  returned ~7370 cycles when a user asked for 6000 at 80 % DoD -- a 23 % error in the fade budget.
  Set both reference fields from the same datasheet line as the cycle count.
- `Params.battery_calendar_rate_per_year` (year): calendar fade from the annual-mean cell
  temperature and the mean SoC. Empirical law (Ali et al. 2023, Front. Energy Res. 11:1108269,
  Table 3): `Q_cal = a1 exp(a2 SoC) b1 exp(b2/T) t^c1`, t in days, T in kelvin. `b2 = -Ea/R`, so LFP
  implies Ea = 29.0 kJ/mol and one fade doubling per 17.6 degC. Mean SoC is taken as `1 - DoD/2`, the
  time-average of a cycle over the usable window. About 0.46-2.11 %/yr over 10-50 degC, 0.85 %/yr at
  25 degC.

**Magnitude anchor.** The Ali fit is a cross-study extrapolation from short tests (120-885 days) and
over-predicts long-duration loss: 8.2 % against the 2-4 % measured on LFP/C cells stored ten years at
6 degC / 50 % SoC (J. Power Sources 2025, S0378775325016155). The code therefore keeps the fit's
SHAPE and renormalises its MAGNITUDE onto that measurement (factor 0.367) -- the same
shape-from-fit / magnitude-from-measurement split the cycle coefficients use with the rated cycle
life. `battery.technical.calendar_fade_scale` moves along the literature band: 1.0 is the anchored
model, ~2.7 recovers the unscaled Ali envelope. The band is 2.7x wide, so report both ends.

**This replaces** the upstream per-hour cubic, which gave 0.05 %/yr at 25 degC -- a 386-year calendar
life, 1-2 orders of magnitude below every measured LFP storage dataset, and low enough that calendar
ageing had no effect on any result.

**Cell vs ambient temperature.** PVGIS reports outdoor air temperature, but the cells sit in an
enclosure that runs hotter. `battery.technical.enclosure_temperature_rise_c` (default 10 K) is added
to the ambient series before BOTH coefficients are evaluated: 10 K suits a ventilated but
unconditioned battery room, ~20 K a sealed container in full sun, 0 active cooling. This is a design
assumption, not a fitted value -- run it as a sensitivity. It is not double counted: the layer-I
cycle fit's own self-heating and pack offset are defined relative to the air around the pack, which
is what this rise produces.

Both fade terms enter the LP additively in the effective-capacity state (§4c). Defaults: LFP, 6000
cycles to 80 % SoH, initial SoH 1.0, DoD 0.8, +10 K enclosure, calendar scale 1.0.
Missing `ambient_temperature.csv` or an unknown chemistry with the flag on raises
`InputValidationError`. So does a calendar lifetime long enough that calendar fade alone would
exhaust `initial_soh - end_of_life_soh`: that combination is LP-infeasible and oversizing cannot
relieve it, because both the fade and the end-of-life floor scale with nameplate energy, so the
error names the longest consistent lifetime for the site instead.

### 4c. Lifetime formulation in the multi-year LP

With `coefficients_enabled`, `battery_effective_energy_capacity` (year × scenario × inv_step) is the
usable energy state in kWh and the two ageing mechanisms add into it:

```
cycle_fade_y    = sum_t c(T_t) * battery_discharge_dc[t, y]       (kWh)
calendar_fade_y = r_cal(T_bar_y) * nameplate_energy               (kWh)
eff_cap_y       = eff_cap_{y-1} - cycle_fade_{y-1} - calendar_fade_{y-1}
eff_cap_y       = SoH0 * nameplate_energy                         (at each commissioning year)
eff_cap_y      >= SoH_eol * nameplate_energy                      (end-of-life floor)
```

The SOC window tracks `eff_cap`, so fade removes usable storage. The end-of-life floor caps a
cohort's cumulative fade at `(SoH0 - SoH_eol) * nameplate`, which is what makes
`cycle_lifetime_to_eol_cycles` binding rather than advisory, and is the channel through which a hot
cluster forces a larger battery. The availability ceiling deliberately carries **no** exogenous
degradation rate in this mode: calendar fade is already in the state, and applying `r_cal` to both
would count it twice.

Battery energy CAPEX is annuitised over `calendar_lifetime_years` only. Cycle ageing carries no
separate wear charge: it is paid through lost usable capacity and the end-of-life floor, so charging
it again would double-count the same physics.

### 4d. Where `calendar_lifetime_years` comes from

That one field does three jobs: it decides when a replacement cohort is commissioned, it sets the
amortisation rate `CRF(wacc, L)`, and it is the horizon the sub-linear calendar law is linearised
over. Nothing in the LP ties it to `end_of_life_soh`, so on its own it is free to contradict the
physics -- and before the calendar recalibration it did, retiring cells at SoH 0.94.

**Leave `battery.investment.by_step.*.calendar_lifetime_years: null` and it is derived per cluster**
as the year the cohort actually reaches end-of-life SoH, by solving

```
calendar_fade(T_cell, SoC, L) + L x cycle_fade_per_year = initial_soh - end_of_life_soh
```

for `L` (bisection; the left side is strictly increasing so the root is unique), then flooring to a
whole year because the replacement masks step in integers. Flooring keeps the cohort inside its
budget, so a derived life can never trip the §4b calendar guard.

Cycling intensity is the one term that is a dispatch decision rather than an input, so it is
estimated before the solve: an off-grid battery sized to carry the design night is discharged every
night, hence

```
equivalent full cycles per year = (annual dark-hour load) x end_of_life_soh / (95th-percentile night)
```

The absolute sizing cancels, which is what makes this computable without the LP. The
`end_of_life_soh` factor accounts for the battery having to carry that night while degraded, which is
the condition a planner sizes for; omitting it understates the nameplate and so overstates cycling by
about `1/SoH_eol`. `c(T)` is then averaged with the dark-hour load as weights rather than flat over
the year, because discharge happens in the cool hours.

Validated against a solved LP on the BDI cluster, which the estimator never sees: 271 cycles/yr
against ~265 realised, cycle fade 1.427 against 1.394 %/yr, implied life **7.32 y against 7.57 y
realised (3.3 %)**. Across sites it gives 9.6 y at a 30 degC cell, 7.3 y at 35, 4.2 y at 45 and
3.2 y at 50.

Every assumption behind a derived value is written into
`settings.battery_model.degradation_model` (`calendar_lifetime_mode`,
`calendar_lifetime_years_derived`, `calendar_lifetime_years_used`,
`assumed_equivalent_full_cycles_per_year`, `assumed_cycle_fade_per_year`,
`discharge_weighted_cycle_fade_coefficient`, `mean_cell_temperature_c`) so each cluster's assumed
life is reportable.

**Set a number instead** when the replacement date is contractual rather than physical -- a warranty
term, an O&M contract, a financing tenor. That is a legitimately different quantity from the service
life, and the model cannot currently represent both at once: one field sets both the replacement date
and the amortisation period.

**Diagnostic.** Whichever route you take, check SoH in the year before a replacement. Close to
`end_of_life_soh` means the interval matches the physics. Far above it means the interval is too
short and you are scrapping healthy cells. The guard catches the other direction.

**Sensitivity.** The derived life inherits `enclosure_temperature_rise_c`, which dominates it: at a
25 degC ambient site the derived life is 12.7 y at +0 K, 7.5 y at +10 K and 4.3 y at +20 K. The
cycling estimate is comparatively well determined (about 3 %) because it comes from the actual load
profile. So deriving the life does not remove that uncertainty, it propagates it into the
replacement schedule and therefore into cost -- which is more honest than hiding it behind a fixed
number, but it makes the enclosure assumption the headline sensitivity of any study.

---

## 5. Techno-economic setup and solve

`config_map.ThesisConfig` maps the thesis `a.yaml` onto the new engine:
off-grid, dynamic multi-year formulation, 20 years from 2025, discount rate 10 %,
**one investment step** (no capacity expansion), PV + battery only (generator disabled,
no lost load allowed).

| Item | Value | Note |
|---|---|---|
| PV capex | 950 €/kW | 0.95 €/W in a.yaml |
| PV inverter | 200 €/kW_ac | THESIS-MAP |
| Battery capex | 650 €/kWh | 0.65 €/Wh |
| Battery inverter | 300 €/kW | THESIS-MAP |
| Battery life | 8 years (calendar) | a.yaml expected lifetime |
| Battery c-rates | charge 0.2/h, discharge 0.25/h | **must be set**: null = 0 in the new engine, which disables the battery |
| Efficiencies | charge/discharge 0.9, DoD 0.8 | |
| O&M | fixed share of capex (PV 1.05 %, battery 3.85 %) | THESIS-MAP |

Fields marked `# THESIS-MAP` in `config_map.py` have no exact 1:1 equivalent in the old
engine and were set to defensible values. **[CHECK]** Review them before production.

Solver: Gurobi 12.0.3 on the cluster (`SOLVER`, `SOLVER_THREADS=1`,
`SOLVER_TIME_LIMIT=18000 s` in `hpc/env.sh`); HiGHS is the open-source default elsewhere.
A cluster counts as solved only if the solver reports optimal with a finite objective;
otherwise `results/error.txt` is written.

**Resolved (17 Sep 2026).** For GHSL_563 (Aug 2026) objective = 786,159 but reported
NPC = 659,178: the old full-export cash-flow reconstruction omitted PV/battery/generator
fixed O&M (fixed in b0df22e). The core export uses the objective directly (correct).
August LCOEs are therefore ~12-17 % too low (Sep/Aug ratio 1.13-1.20).

**Batteries start full** (by design). With a zero solar series the model does not become
infeasible: it builds a huge battery that starts full (Aug 2026: 7 clusters, LCOE ~1,500
€/kWh, caused by the old zero-sun PVGIS fallback, now removed). The input guard in
`input_prep` rejects all-zero series.

---

## 6. Dependency on the old engine (to remove)

`engines/old` is needed only for:
- `microgridspy.utils.archetypes.demand_calculation` (+ 110 archetype `.xlsx` files);
- `microgridspy.utils.pvgis.download_pvgis_pv_data`.

Side effects: `archetypes.py` imports **streamlit** at load time (unused), so the cluster
environment needs streamlit; the Excel profiles are re-read for every cluster, twice per tier.

**Plan:** move the two functions into `mgpy2` (plain Python, no streamlit), convert the
archetype profiles to one small CSV/parquet table, verify identical output on reference
clusters (task 563 etc.), then delete `engines/old`.

---

## 7. Outputs

Profile `core` (default, `EXPORT_PROFILE` in `hpc/env.sh`):
- `results/summary.json`: meta, metrics (NPC, LCOE, investment), sizing. **This file is
  the completion marker** used by `run_cluster` (skip if present), `monitor_jobs.sh` and
  `make_failed_task_list.py`.
  `meta.run` holds the provenance: code version (`-dirty` = uncommitted changes), solver,
  solver version and parameters, status, prep/solve seconds, full `ThesisConfig`
  (incl. `demand_growth_mode`), host, SGE job/task id, finish time.
- `results/dispatch.parquet`: hourly operation (CSV if pyarrow is missing; none with
  `DISPATCH_FORMAT=none`). The hourly dispatch is **not unique** (many schedules have the
  same cost): use only totals, never hourly profiles, for analysis.
- on failure: `results/error.txt`.

Everything else (energy balance, KPIs, cash flows, reporting summary, Excel) can be rebuilt
offline with `python -m mgpy2.reporting --cat <id>`.

Inputs stay in `projects/<cat>/inputs/` (~5 MB per cluster: demand + resource, 20 year columns each).

---

## 8. Branches and versions

- `main` is the only long-lived branch: it is always the version to run on the cluster.
- Work happens on short-lived branches (e.g. `fix-pvgis`), merged into `main` through a
  GitHub pull request.
- Every production run gets a **tag** on the exact commit it used (e.g. `eth-aug2026`),
  and the tag name is recorded with the results. Tags, not branches, link results to code.

## 9. Where to look

| Question | File |
|---|---|
| Which country / resources / solver? | `hpc/env.sh` |
| Task id → cluster | `mgpy2/sample.py` |
| One cluster end-to-end | `mgpy2/run_cluster.py` |
| Techno-economic values | `mgpy2/config_map.py` (`ThesisConfig`) |
| Demand + solar inputs | `mgpy2/input_prep.py` (+ `engines/old/.../archetypes.py`, `pvgis.py`) |
| Model equations | `engines/new/core/multi_year_model/`, `engines/new/docs/Mathematical_Formulation.pdf` |
| Aggregating results | `mgpy2/postprocess.py`, `mgpy2/reporting.py` |
| Running on CFDHub | `hpc/GUIDE.md` |
