# Changelog

## v1.0.0 — 2026-09 (first public release)
### Data
* 100 household, 5 health-facility and 1 school archetype, hourly series + household 1-minute series.
* **Health facilities T2 and T3 corrected (2025)** with respect to the version used in the 2023 paper and shipped with RAMP-Streamlit (as of September 2026):
  * X-ray: rated power 32 000 W → 1 300 W with ±80 % power variability (`thermal_p_var` 0.8) — same use time and occasional use.
  * T3 input file re-implemented: refrigeration as 12 × 250 W fridges with three explicit duty cycles, water dispenser (2 × 200 W, duty-cycled), HVAC as a 12 kW duty-cycled compressor running 24/7. The pre-2025 input files were not preserved, so further differences cannot be itemised.
  * Effect on annual energy: T2 13.8 → 14.3 MWh; T3 151.1 → 159.8 MWh; T4/T5 follow T3 (×1.5, ×2).
* T1 and school unchanged.
* **Households HH_NC_F1_T1, HH_NC_F1_T2, HH_NC_F1_T3 re-simulated.** The earlier series of these three archetypes were the outputs of the next tier's input (tier 2, 3 and 4 respectively): annual energy per 100 households 5.12 → 3.87, 8.91 → 5.03, 75.41 → 8.84 MWh. Re-simulated from the preserved inputs with the legacy RAMP engine shipped in `code.zip` (default settings, seeds 20260918 + month). Validation and comparison with sibling archetypes in `regeneration_check.csv`. The other 97 household series are unchanged.
### Classification
* Health-facility tiers redefined by function and size with a crosswalk to Kenyan and Ugandan levels of care (`health_facility_tiers.csv`, `facility_type_crosswalk.csv`). This **replaces the labels of the 2023 paper** (where T5 was described as "sub-county hospital") and the 2019 M-LED facility-type mapping.
### Inputs
* RAMP inputs published as Excel (canonical rampdemand format) and as the original legacy Python files.
