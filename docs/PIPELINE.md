# How the pipeline works

_Owner's guide to what happens between a row of the country sample and a solved mini-grid.
Written 16 Sep 2026 from the code on `development` and the thesis methodology
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
        │        └─ solar      mgpy2/input_prep.compute_resource_cf  ─► inputs/resource_availability.csv
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
- **The archetype profiles are fixed files.** RAMP (stochastic) was used once to create
  them; no RAMP runs here. Clusters with the same zone, cooling type and tier mix have
  **identical hourly shapes**, differing only in scale.
  **[CHECK]** The thesis says "demand time series are generated using the RAMP model".
  Make sure the paper describes it as *archetype profiles generated with RAMP*.
- **Zone boundaries are sharp:** neighbouring clusters either side of 10°N (crossing
  Ethiopia) get different profiles.
- **[CHECK]** Households are divided by 100 (one archetype file = 100 households?),
  hospitals are not. Confirm against the archetype source
  ("Archetypes of Rural Users in Sub-Saharan Africa for Load Demand Estimation").
- **[CHECK]** A missing `cooling` value becomes `NC` (no cooling). Count how many rows
  this affects; in hot areas it underestimates demand.

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
**[CHECK]** Every result should record which mode it used (`summary.json` → `meta`).

---

## 4. Solar resource

`input_prep.compute_resource_cf` → old-engine `pvgis.download_pvgis_pv_data`.

- Downloads the PVGIS **typical meteorological year** for the cluster's lat/lon
  (`https://re.jrc.ec.europa.eu/api/tmy`), tilt 10°, azimuth 180°, with a temperature correction.
- PV output for a 1000 W array ÷ 1000 → hourly **capacity factor**; the same year is
  repeated for all 20 years → `inputs/resource_availability.csv`.
- **Needs internet on the node** in `RUN_MODE=full`. With offline nodes: prepare on the
  login node (`orchestrator.py --prepare-only`), then run with `RUN_MODE=solve-only`.
- **[TODO] Today a failed download silently becomes ZERO sun** (`allow_zero_fallback=True`,
  just a `WARN` line). PV+battery clusters then fail as infeasible; with a generator they
  would silently become diesel-only. Planned fix: fail loudly so the task goes on the
  rerun list.
- PVGIS updates its database over time, so re-downloading later may not give identical inputs.

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

**[CHECK]** For GHSL_563 (Aug 2026), objective = 786,159 but reported NPC = 659,178.
Understand the difference (salvage? penalties? discounting?) before publishing LCOE.

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
- `results/dispatch.parquet`: hourly operation (CSV if pyarrow is missing).
- on failure: `results/error.txt`.

Everything else (energy balance, KPIs, cash flows, reporting summary, Excel) can be rebuilt
offline with `python -m mgpy2.reporting --cat <id>`.

Inputs stay in `projects/<cat>/inputs/` (~5 MB per cluster: demand + resource, 20 year columns each).

---

## 8. Where to look

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
