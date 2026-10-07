# Load-demand archetypes for rural users in Sub-Saharan Africa
### Households, health facilities and schools — RAMP inputs and hourly/minute time series (v1.0.0)

**Author:** Nicolò Stevanato (Politecnico di Milano, Department of Energy; ORCID [0000-0002-3419-0389](https://orcid.org/0000-0002-3419-0389))
**DOI:** https://doi.org/10.5281/zenodo.22832973  
**Licence:** data CC-BY-4.0 · code EUPL-1.2 (see `LICENSE.md`)
**Method paper:** N. Stevanato, I. Sangiorgio, R. Mereu, E. Colombo, *Archetypes of Rural Users in Sub-Saharan Africa for Load Demand Estimation*, 2023 IEEE PES/IAS PowerAfrica. https://doi.org/10.1109/POWERAFRICA57932.2023.10363287

---

## 1. What this is

A library of **106 synthetic electricity-demand archetypes** for data-scarce rural areas of Sub-Saharan Africa (SSA), meant as a quick, modular input for mini-grid sizing (e.g. MicroGridsPy), energy-system and geospatial electrification models. All profiles were generated with the bottom-up stochastic model **RAMP** (Lombardi et al., 2019).

| User type | Archetypes | Dimensions | Series represents |
|---|---|---|---|
| Households | 100 | 4 cooling regimes × 5 latitude zones × 5 wealth tiers | **100 households** (aggregate) |
| Health facilities | 5 | 5 facility tiers | **1 facility** |
| School | 1 | rural primary school | **1 school** |

A village load is built by linear combination (paper, Eq. 1):

`P_village(t) = Σ_tiers (N_households,tier / 100) · P_HH(t) + Σ_tiers N_HF,tier · P_HF(t) + N_school · P_SCH(t)`

using one cooling regime and one latitude zone for the village. See `code/example_community_load.py`.

## 2. Choosing an archetype

* **Latitude zone** (`latitude_zones.csv`): F1 10–20°N · F2 10°N–10°S · F3 10–20°S · F4 20–30°S · F5 south of 30°S. Latitude sets the monthly sunrise/sunset and hence lighting windows. *Not covered: north of 20°N.*
* **Cooling regime** (`cooling_regimes.csv`): NC none · AY all year · OM October–March · AS April–September. Cooling appliances (fans; air conditioner in tier 5) are only present in cooling months. Tiers 1–2 own no cooling appliances, so their inputs are identical across regimes (the series are independent stochastic draws).
* **Household wealth tier** 1 (poorest) – 5 (wealthiest): appliance baskets in `household_tiers.csv`.
* **Health-facility tier** 1–5: defined by function and indicative size, cross-walked to Kenyan (KEPH levels) and Ugandan (MoH) systems in `health_facility_tiers.csv`. To map a facility list to tiers, use `facility_type_crosswalk.csv` (bed count overrides names). **HF_T5 is not representative of regional/national referral hospitals (>100 beds).**

If your facility data come from the JRC Electricity-access Health Facility Database (EHFDB; Moner-Girona et al., 2021), use its four categories directly: primary health post → T1, first hospital → T3 (T4 above 45 beds), secondary hospital → T5, tertiary referral → out of scope. EHFDB has no separate health-centre class, so T2 is only reachable with extra information (maternity/inpatient services).

`archetype_index.csv` lists all 106 archetypes with annual energy, peak, load factor and, for health facilities, level of care, indicative beds and scope.

## 3. Files

| File | Content |
|---|---|
| `archetype_index.csv` | One row per archetype: IDs, dimensions, annual kWh, peak W, load factor, derivation |
| `households_hourly.csv` | 8760 h × 100 household archetypes [W, per 100 households] |
| `health_facilities_hourly.csv` | 8760 h × HF_T1..HF_T5 [W, per facility] |
| `school_hourly.csv` | 8760 h × SCH_T1 [W, per school] |
| `households_minute.zip` | 4 Parquet files (one per cooling regime), 525 600 min × 25 archetypes [W, per 100 households] — raw RAMP output |
| `inputs_ramp_excel.zip` | RAMP inputs as Excel, **one file per archetype per month** (`<group>/<ID>/month_MM.xlsx`), canonical rampdemand headers; + `conversion_adjustments.csv` |
| `inputs_ramp_legacy_py.zip` | The legacy RAMP Python input files as run (households, HF_T2, HF_T3) |
| `code.zip` | Build, conversion and check scripts; legacy RAMP engine v0.2.1 used for the households; usage example |
| `household_tiers.csv`, `health_facility_tiers.csv`, `facility_type_crosswalk.csv`, `latitude_zones.csv`, `cooling_regimes.csv` | Classification tables |
| `data_dictionary.csv` | Column definitions and units |
| `reproducibility_check.csv` | Re-run of a sample of archetypes from the Excel inputs with rampdemand 0.5 (see §5) |
| `regeneration_check.csv` | Full-year re-simulation with the legacy engine: validation and the three corrected archetypes (see §5) |

### Units and time convention
* Values are **mean power over the hour in W** (= Wh in that hour). Annual energy [kWh] = column sum / 1000.
* **Synthetic 365-day year** (`hour_of_year` 0–8759), no calendar date. Hours are local solar time of the archetype.
* **Weekends:** RAMP was run month by month and the weekly pattern restarts every month: **days 6–7, 13–14, 20–21, 27–28 of each month are weekend days**. This matters for appliances used only on weekdays (health facility T1, school) or weekends (household iron). Re-align if you need a real calendar.
* School: 14-day holidays in April and August (represented by 50 % occasional use), closed in November–December.

### Software needed
* CSV files: any tool. Parquet files (`households_minute.zip`): Python with `pandas` + `pyarrow` (or R `arrow`).
* Excel inputs with rampdemand 0.5: Python 3.11, `rampdemand==0.5.0`, `pandas==1.5.3`, `numpy==1.24.4`, `openpyxl` (the versions pinned by RAMP-Streamlit; rampdemand 0.5 fails with pandas ≥ 2).
* Legacy engine (`code/legacy_run.py`, `code/regen.py`): same environment (it needs numpy < 2).

## 4. Two ways to use the archetypes

**A. Use the time series as they are** (quickest). Combine the hourly profiles as in §1 (`code/example_community_load.py`). Good for sizing and planning studies of villages of roughly 50 households or more.

**B. Re-generate them stochastically for your village.** Load the Excel inputs in RAMP, set `num_users` to the real number of households per tier (or facilities), and run it. This gives a new random draw with the diversity of *your* village size. It is the better choice for small villages (the 100-household profiles understate the peak of a few tens of households) or when you need several independent draws.
- With the graphical interface **RAMP-Streamlit**: https://github.com/SESAM-Polimi/RAMP-Streamlit. It accepts at most 4 seasons per run, so choose up to 4 representative months (or group months into seasons).
- With **rampdemand** in Python (code below), one file per month.

Profiles re-generated with current RAMP versions are statistically consistent with the published ones but 4–15 % lower in energy (§5).

### Running the RAMP inputs

Each Excel file is a complete RAMP input for one month. With rampdemand (tested with 0.5.0, pandas 1.5.x):

```python
from ramp import UseCase
uc = UseCase(name="HH_AY_F1_T3_jan", date_start="2021-01-01", date_end="2021-01-31")
uc.load("inputs_ramp_excel/households/HH_AY_F1_T3/month_01.xlsx")
profile_W = uc.generate_daily_load_profiles()   # 1-min, W
```

The same files can be uploaded to RAMP-Streamlit (https://github.com/SESAM-Polimi/RAMP-Streamlit). Note that RAMP-Streamlit supports at most 4 seasons, so the 12 monthly files must be grouped by the user.

**Conversion notes.** The Excel files were generated automatically from the legacy inputs (`code/convert_to_ramp_excel.py`) by executing each legacy file against a recorder of the legacy API, so parameters are transferred exactly. Two deliberate changes: (i) unused placeholder windows were dropped (`num_windows` = number of active windows, as used by the legacy engine); (ii) where `func_cycle` exceeded 99 % of the shortest possible window time, it was reduced to that value, because rampdemand ≥ 0.5 raises an error in that case while legacy RAMP silently capped the use time. All such changes (198 appliance-months) are listed in `conversion_adjustments.csv`.
HF_T1 and the school had no machine-readable input: their Excel files were built from the documented parameter tables; the HF_T1 fridge duty cycle is **reconstructed** from HF_T2.

## 5. Reproducibility — read this

The time series are the reference data of this record. How well they can be regenerated from the published inputs:

* **Households, legacy engine (shipped in `code.zip`)**: reproduced within stochastic noise. Full-year validation: HH_NC_F2_T1 +0.5 %, HH_NC_F2_T3 −1.5 % annual energy, average-day profile correlation 1.00 (`regeneration_check.csv`).
* **Households, rampdemand 0.5**: 4–9 % lower energy for tiers 3–5 and about 15 % lower for tier 1 (`reproducibility_check.csv`, 28 January days). The RAMP engine changed between versions; use the legacy engine for exact comparability.
* **Health facilities and school, rampdemand 0.5**: HF_T2 −11 %, HF_T3 −18 % (−11 % with the legacy engine). HF_T1 −17 % and school −25 %: their Excel inputs were rebuilt from the documented parameter tables (no original input file survived), so treat them as approximate.

**Correction in v1.0.0 — HH_NC_F1_T1, T2, T3.** In the series used in the 2023 paper, the three lowest tiers of the no-cooling, 10–20°N archetype contained the results of the *next* tier's input (annual energy per 100 households: 5.12 / 8.91 / 75.41 MWh instead of ~3.9 / ~5.0 / ~8.9 MWh; the T1 series also showed the tier-2 morning lighting). The input files were correct. The three archetypes were re-simulated in this release from their preserved inputs with the legacy engine (default settings, seeds 20260918 + month number; `code/regen.py`). They now agree with their siblings with identical inputs (HH_AY/OM/AS_F1_T1 and T2) and with no-cooling tier 3 in other zones. **Users of earlier copies (the 2023 paper, RAMP-Streamlit ≤ September 2026, derived studies) should note that these three profiles changed.**

## 6. Provenance and history
* 2021–2023: household archetypes (legacy RAMP v0.2.1-pre, monthly input files, 100 households per run); health facilities and school after Stevanato et al. (2020, M-LED/Kenya).
* 2026 (v1.0.0): HH_NC_F1_T1–T3 re-simulated (see §5).
* 2025: health facilities T2 and T3 corrected (see `CHANGELOG.md`); T4 and T5 re-derived as 1.5× and 2× T3.
* Benchmark note: EHFDB (Moner-Girona et al., 2021) assigns much lower standard demands per category (1,825 / 7,300 / 14,600 / 91,250 kWh/yr for health post / first / secondary / tertiary hospital, from ESMAP Multi-Tier Framework tiers 2–5) than the RAMP archetypes here (e.g. HF_T3 ≈ 160,000 kWh/yr). The difference comes mainly from space cooling, 24/7 inpatient services and imaging, which the archetypes include. Choose consistently with your study's service assumptions.
* Cross-check: WRI Uganda (Sinclair-Lecaros et al., 2023, https://doi.org/10.46830/writn.21.00093) — HF_T1 ≈ Uganda archetype A1 (HC II), HF_T3 ≈ A11 and HF_T4 ≈ A14 (HC IV).

## 7. Limitations
* Synthetic profiles, not measurements. One stochastic realisation per archetype.
* Each household profile aggregates 100 households: scaled down to a small village it understates the peak (less diversity in reality). For villages below ~50 households, treat the peak as a lower bound or re-generate with the real number of users (§4 B).
* HF_T4/HF_T5 are deterministic scalings of HF_T3: same shape, no additional diversity.
* Latitude affects lighting only; climate affects cooling appliances only; no demand growth, no productive uses.
* Coverage: SSA between 20°N and the southern tip; not the Sahel north of 20°N.

## 8. Sources for the health-facility classification
* WHO, World Bank, SEforALL, IRENA (2023). *Energizing health: accelerating electricity access in health-care facilities.* https://www.seforall.org/system/files/2023-01/energizing_health.pdf
* Moner-Girona, M. et al. (2021). Achieving universal electrification of rural healthcare facilities in sub-Saharan Africa with decentralized renewable energy technologies. *Joule* 5(10), 2687–2714. https://doi.org/10.1016/j.joule.2021.09.010 — EHFDB data: https://data.jrc.ec.europa.eu/collection/id-0076
* Maina, J. et al. (2019). A spatial database of health facilities managed by the public health sector in sub-Saharan Africa. *Scientific Data* 6, 134. https://doi.org/10.1038/s41597-019-0142-2
* Sinclair-Lecaros, S., Mentis, D., Mulepo C.S., E.S., Falchetta, G., Stevanato, N. (2023). *A GIS-based demand assessment methodology to estimate electricity requirements for health care facilities: a case study for Uganda.* WRI Technical Note. https://doi.org/10.46830/writn.21.00093
* Kenya levels of care (KEPH levels 1–6), Ministry of Health, Kenya; summarised e.g. in https://www.the-star.co.ke/business/2023-07-18-explainer-six-levels-of-hospitals-and-services-they-offer

## 9. How to cite
Please cite this dataset, https://doi.org/10.5281/zenodo.22832973 (see `CITATION.cff`), **and** the method paper above. RAMP: F. Lombardi, S. Balderrama, S. Quoilin, E. Colombo (2019), *Generating high-resolution multi-energy load profiles for remote areas with an open-source stochastic model*, Energy 177, 433–444, https://doi.org/10.1016/j.energy.2019.04.097.

## 10. Acknowledgements
The 2023 archetypes were developed in the framework of the SETaDiSMA project, part of the LEAP-RE programme, which received funding from the European Union's Horizon 2020 Research and Innovation Programme under Grant Agreement 963530.

Contact: nicolo.stevanato@polimi.it
