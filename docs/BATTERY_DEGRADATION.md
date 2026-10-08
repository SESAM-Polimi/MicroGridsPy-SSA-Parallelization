# Battery degradation and economic lifetime — complete reference

Multi-year (`dynamic`) formulation, Li-ion only, as implemented in `engines/new`.
Covers the physics, the coefficient data and its provenance, the full optimisation model, the
economic-lifetime logic, every input parameter, and the complete list of assumptions and
limitations.

This document is self-contained: every symbol is defined, every equation is written out, and every
number quoted is either an input default or a measured result from a solved run.

---

## 1. What the module does, and what it does not

The module answers one question: **how much battery nameplate energy must be installed so that the
system still meets demand after the battery has aged?**

It does that by carrying a single state variable — usable energy in kWh — that shrinks as the
battery ages, and by forcing that state to stay above an end-of-life threshold. Ageing has two
causes, both temperature-driven, and both are read off pre-fitted curves evaluated at the cluster's
own hourly temperature series.

It is **not** a cell model. It does not simulate electrochemistry, it does not track voltage,
resistance or temperature dynamics, and it does not predict absolute lifetimes to better than tens of
percent. It is a planning-grade instrument designed to rank sites and to size storage consistently
across thousands of clusters.

### 1.1 The one-paragraph summary

A battery of nameplate energy `E_nom` starts with usable energy `SoH₀ · E_nom`. Every year it loses
capacity for two reasons: **calendar fade** (it ages on the shelf, faster when hot and when held at
high charge) and **cycle fade** (it ages per kWh pushed through it, faster when hot). The two add.
The state-of-charge window rides on the shrinking usable energy, so ageing directly removes storage.
A hard floor forbids the usable energy from dropping below `SoH_eol · E_nom`. Degradation is never
charged a price; it costs money only because a faded battery forces you to buy more nameplate, and
nameplate is what the objective pays for. The battery is replaced on a fixed interval, which by
default is derived from the physics as the year the battery actually reaches its end-of-life state.

---

## 2. Notation

All energies are kWh, all powers kW, all temperatures °C unless marked K, all fades are fractions of
**nameplate** energy (not of current capacity).

### 2.1 Indices and sets

| Symbol | Set | Meaning |
|---|---|---|
| `t` | `period` | hour within a year, 1…8760 |
| `y` | `year` | project year, 1…Y (Y = 20 by default) |
| `s` | `scenario` | stochastic scenario, weight `w_s`, `Σ w_s = 1` |
| `k` | `inv_step` | investment step (cohort of installed capacity) |
| `r` | `resource` | renewable resource (Solar) |
| `g` | `battery_loss_segment` | piecewise segment of the conversion-loss epigraph (`convex_loss_epigraph` only) |

### 2.2 Decision variables (battery, coefficients mode)

| Symbol | Code name | Dims | Unit | Bound | Meaning |
|---|---|---|---|---|---|
| `U` | `battery_units` | k | – | ≥ 0 | installed units of nameplate energy |
| `P_inv` | `battery_inverter_power` | k | kW | ≥ 0 | installed battery-inverter power |
| `P_ch` | `battery_charge` | t,y,s,k | kWh/h | ≥ 0 | AC-side charge |
| `P_dis` | `battery_discharge` | t,y,s,k | kWh/h | ≥ 0 | AC-side discharge |
| `P_ch^dc` | `battery_charge_dc` | t,y,s,k | kWh/h | ≥ 0 | DC-side (internal) charge, epigraph only |
| `P_dis^dc` | `battery_discharge_dc` | t,y,s,k | kWh/h | ≥ 0 | DC-side (internal) discharge, epigraph only |
| `L_ch` | `battery_charge_loss` | t,y,s,k | kWh/h | ≥ 0 | charge conversion loss, epigraph only |
| `L_dis` | `battery_discharge_loss` | t,y,s,k | kWh/h | ≥ 0 | discharge conversion loss, epigraph only |
| `S` | `battery_soc` | t,y,s,k | kWh | ≥ 0 | stored energy |
| `E` | `battery_effective_energy_capacity` | y,s,k | kWh | ≥ 0 | **usable energy state** |
| `F^cyc` | `battery_cycle_fade` | y,s,k | kWh/yr | ≥ 0 | annual capacity lost to cycling |
| `F^cal` | `battery_calendar_fade` | y,k | kWh/yr | ≥ 0 | annual capacity lost to calendar ageing |

With `loss_model = constant_efficiency` (the SSA pipeline default) the four epigraph-only rows are
not variables: `P_ch^dc` and `P_dis^dc` are the linear expressions `η_ch · P_ch` and `P_dis / η_dis`
(§4.3).

Sixty variables in total for the degradation layer at Y = 20, one scenario, one step
(3 × 20). The whole degradation layer costs **60 variables and 101 constraints** on top of a
model with no degradation at all — measured, see §10.

### 2.3 Exogenous coefficients (computed in the data pipeline, constant in the LP)

| Symbol | Code name | Dims | Unit | Meaning |
|---|---|---|---|---|
| `c(T_t)` | `battery_cycle_fade_coefficient` | t,y,s | kWh lost per kWh discharged | marginal cycle-fade cost |
| `r_cal` | `battery_calendar_rate_per_year` | y | 1/yr | linear-equivalent calendar fade rate |
| `T_t^amb` | `ambient_temperature` | t,y,s | °C | PVGIS outdoor air temperature |
| `a_g, b_g` | `battery_charge_loss_slope/intercept` | g | – | conversion-loss epigraph segments (epigraph only) |

### 2.4 Scalar parameters

| Symbol | Code name | Default | Unit |
|---|---|---|---|
| `E_unit` | `nominal_capacity_kwh` | 1.0 | kWh/unit |
| `δ` | `depth_of_discharge` | 0.8 | – |
| `σ₀` | `initial_soc` | 1.0 | – |
| `SoH₀` | `initial_soh` | 1.0 | – |
| `SoH_eol` | `end_of_life_soh` | 0.8 | – |
| `B` | — (derived: `SoH₀ − SoH_eol`) | 0.2 | – |
| `N_rated` | `cycle_lifetime_to_eol_cycles` | 6000 | cycles |
| `δ_ref` | `cycle_life_reference_dod` | 0.8 | – |
| `T_ref` | `cycle_life_reference_temperature_c` | 25 | °C |
| `ΔT_encl` | `enclosure_temperature_rise_c` | 10 | K |
| `φ_cal` | `calendar_fade_scale` | 1.0 | – |
| `L` | `calendar_lifetime_years` | derived | yr |
| `ρ_ch, ρ_dis` | `max_charge/discharge_c_rate` | 0.2, 0.25 | 1/h |
| `η_ch, η_dis` | `charge/discharge_efficiency` | 0.9, 0.9 | – |
| `C_E` | `specific_investment_cost_per_kwh` | 650 | €/kWh |
| `C_P` | `inverter_specific_investment_cost_per_kw` | 300 | €/kW |
| `i` | `wacc` | 0.10 | 1/yr |
| `L_inv` | `inverter_lifetime_years` | 8 | yr |
| `f_om` | `fixed_om_share_per_year` | 0.0385 | 1/yr |

---

## 3. The coefficient curves: where they come from and what they mean

### 3.1 Cycle fade — the depth-resolved curve `Ψ(D, T)`

**Source.** `engines/new/core/data_pipeline/layer1_liion_coefficients.json`, key `ck_bands`, copied
verbatim from upstream MicroGridsPy `b76a32d`. It is a distillation of an offline Layer-I
electro-thermal cell model onto a compact polynomial form.

**Form.** The usable depth axis `D ∈ [0, 1]` is divided into ten 10 % slices with edges
`0.0, 0.1, …, 1.0`. Each slice `j` carries a cubic in `u = T/10`:

```
κ_j(T) = p_j1·u³ + p_j2·u² + p_j3·u + p_j4 ,      u = T[°C]/10
```

`κ_j` is the **marginal** fade of traversing slice `j`: fraction of nameplate lost per unit of
depth-fraction crossed. The cumulative per-cycle fade is the integral:

```
Ψ(D, T) = Σ_{j: slice below D}  κ_j(T) · (width of slice j within [0, D])
```

evaluated by linear interpolation between slice edges. `Ψ(D, T)` is the **fraction of nameplate
capacity lost by one discharge of depth `D` at temperature `T`**.

**What the data actually says.** At 25 °C the ten LFP slice slopes are, in units of 10⁻⁶:

```
33.521  33.698  33.844  33.959  34.043  34.078  34.078  34.078  34.078  34.078
```

Two facts follow, and both matter:

1. **Depth is almost irrelevant.** Deepest ÷ shallowest = **1.0166**, and the curve is perfectly
   flat beyond `D = 0.5`. For NMC the ratio is 1.0306. So `Ψ` is within 1.7 % of linear in `D`.
   Consequence: **total throughput determines lifetime, and the depth of individual cycles does
   not.** Measured: energy throughput to end-of-life is 5892 full-DoD-equivalent cycles at
   `δ = 1.0` and 5925 at `δ = 0.4` — flat to 0.6 %. Operating at 40 % DoD buys you 2.5× as many
   *cycles* (14 812 vs 5 892) but exactly the same total kWh.
2. **Temperature dominates.** `c` rises **4.28×** from 10 °C to 45 °C, one doubling per 16.7 °C.

This is why the SOC-band formulation was removed: it resolved a 1.7 % effect at a cost of 1.58 M
LP variables and changed the optimum by 0.007 %.

**Marginal coefficient used by the LP.** Only the mean slope over the operating window is needed:

```
c(T) = Ψ(δ, T) / δ  ·  λ_cyc
```

Units: fraction of nameplate lost per unit depth-fraction traversed. Because a discharge of `ΔE`
kWh from a battery of nameplate `E_nom` corresponds to a depth increment `ΔE/E_nom`, and the fade in
kWh is `E_nom · c · ΔE/E_nom = c · ΔE`, the coefficient is dimensionally **kWh of capacity lost per
kWh discharged**, independent of `E_nom`. That is what makes the LP linear.

**Magnitude calibration `λ_cyc`.** The shipped `Ψ` shape was fitted at **full depth and 25 °C**
(`layer1` metadata: `test_DOD = 1.0`, `test_T_C = 25`, `N_cycles = 6000`, `EOL_SOH = 0.8`). The
magnitude is re-pinned to the user's datasheet number by solving

```
N_rated · Ψ_raw(δ_ref, T_ref) · λ_cyc = B           ⇒   λ_cyc = B / (N_rated · Ψ_raw(δ_ref, T_ref))
```

so that **exactly `N_rated` cycles at `δ_ref` and `T_ref` take the cell from `SoH₀` to `SoH_eol`**.
At the defaults `λ_cyc = 1.22865`. Verified behaviour:

| asked for | delivered |
|---|---|
| 6000 cycles @ 80 % DoD, 25 °C | 6000 |
| 3000 cycles @ 80 % DoD, 25 °C | 3000 |
| 6000 cycles, `SoH_eol = 0.70` (B = 0.30) | 6000, consuming 30 % |

Upstream used `λ = N_ref/N_rated` with a hard-coded `N_ref`, which silently delivered **7372**
cycles when a user asked for 6000 at 80 % DoD — a 23 % error in the fade budget. That is fixed.

**Thermal convention — do not double count.** The `layer1` protocol states the cycle law is
evaluated at `T_env + self-heating(c-rate) + dt_pack`, with `distillation_crate = 0.4` and
`dt_pack = 3.0` K. **Cell self-heating and the pack-to-air offset are already inside the cubic.**
The correct input is therefore the air temperature *around the pack*. `ΔT_encl` (§3.3) supplies the
step from outdoor air to enclosure air, which is a different and additional quantity.

### 3.2 Calendar fade

**Source (shape).** Ali et al. (2023), *Frontiers in Energy Research* 11:1108269, Table 3 — a
semi-empirical cross-study fit to published Li-ion storage datasets (Naumann 2018, Keil 2016,
Eddahech 2015, Geisbauer 2021).

```
Q_cal(SoC, T, τ) = α₁ · exp(α₂·SoC) · β₁ · exp(β₂/T_K) · τ^γ
```

with `τ` in **days**, `T_K` in **kelvin**, and `Q_cal` a fraction of nameplate capacity lost.

| chemistry | α₁ | α₂ | β₁ | β₂ | γ |
|---|---|---|---|---|---|
| LFP | 0.00157 | 1.317 | 142 300 | −3492 | 0.48 |
| NMC | 0.03304 | 0.5036 | 385.3 | −2708 | 0.51 |

**Interpretation of each term.**

- `τ^γ` with `γ = 0.48`: diffusion-limited growth of the solid-electrolyte interphase — ageing is
  roughly square-root in time, so the *first* year is much worse than the twentieth.
- `exp(β₂/T_K)`: Arrhenius. `β₂ = −E_a/R`, so LFP implies **E_a = 29.0 kJ/mol** (literature range
  for LFP SEI growth is 20–50) and **one fade doubling per 17.6 °C** at 25 °C.
- `exp(α₂·SoC)`: holding charge accelerates ageing. `exp(1.317) = 3.73×` from empty to full.
  Naumann et al. measured roughly 2× on LFP; same order.

**Form validation.** The published equation is not legible in the available copy, so the form above
was reconstructed and checked against the paper's own Figure 5: at 0 °C, SoC 0, 50 years it gives
**6.96 %** against **7 %** published. The three independent physical checks above (activation
energy, time exponent, SoC multiplier) all land in the expected range.

**Magnitude anchor.** The Ali fit is built on tests of 120–885 days and extrapolated, and it
over-predicts long-duration loss. Independent check: Sony/Murata LFP/C cells stored **ten years at
6 °C and 50 % SoC retained 96–98 %** of capacity (*J. Power Sources* 2025, S0378775325016155). The
raw fit predicts 8.17 % loss there — 2.7× pessimistic.

The module therefore keeps the fit's **shape** and renormalises its **magnitude** onto that direct
measurement:

```
λ_cal = 0.03 / Q_cal^raw(SoC = 0.5, T = 6 °C, τ = 10 y) = 0.36720     (= 1/2.72)
```

This is the same shape-from-fit / magnitude-from-measurement split used for cycle fade. It
reproduces the anchor exactly (3.000 % against the 3.0 % target). `φ_cal` (`calendar_fade_scale`)
then walks the literature band: **1.0 is the anchored model, 2.72 recovers the unscaled Ali
envelope**. At 25 °C the two bracket 0.76 %/yr and 2.07 %/yr.

**Mean SoC.** The fit needs a state of charge. The module uses the time-average SoC of a battery
cycling over the usable window `[1 − δ, 1]`:

```
SoC_mean = 1 − δ/2        ( = 0.6 at δ = 0.8 )
```

This replaces upstream's discontinuous step at `δ ≥ 0.75`, which made calendar fade jump 30 % between
`δ = 0.74` and `δ = 0.76`.

**Linearisation to an annual rate.** The LP carries one rate per year, but the law is sub-linear in
time. The rate reproduces the correct **cumulative** loss at the battery's life `L`:

```
r_cal = φ_cal · λ_cal · Q_cal^raw(SoC_mean, T̄_cell, 365·L) / L
```

So total calendar fade at end of life is exact, at the cost of straightening the trajectory inside
a cohort's life (early years under-counted, late years over-counted). Note `r_cal` therefore
*depends on* `L`: a shorter life gives a higher average rate.

**What replaced what.** Upstream evaluates a hard-coded per-hour cubic in ambient temperature,
giving **0.052 %/yr at 25 °C — a 386-year calendar life**, one to two orders of magnitude below
every measured LFP storage dataset. That same `.py` also disagrees by a factor of ~6 with the
`alpha_poly` block inside its own JSON. Both are deleted. The anchored model gives **0.85 %/yr at a
25 °C cell**.

### 3.3 Cell temperature versus ambient

PVGIS reports 2 m **outdoor air** temperature. Cells sit inside an enclosure that runs hotter:

```
T_t^cell = T_t^amb + ΔT_encl
```

`T^cell` drives **both** coefficient curves. Guidance for `ΔT_encl`:

| situation | value |
|---|---|
| actively cooled / air-conditioned | 0 K |
| ventilated, shaded battery room | 5–10 K |
| sealed metal container in full sun | 15–20 K |

**This is a design assumption, not a fitted value**, and it is the single most influential input in
the whole module — see §9.2. At a 25 °C ambient site the derived service life is 12.7 y at +0 K,
7.5 y at +10 K and 4.3 y at +20 K.

### 3.4 Conversion losses (`battery_efficiency_curve.csv`)

Used only with `battery_model.loss_model = convex_loss_epigraph`. Degradation no longer needs it:
the cycle-fade throughput only needs the DC-side discharge, which the constant-efficiency model
also defines (§4.3). The CSV holds `relative_power_pu` and two columns interpreted as **normalised multipliers on
the YAML base efficiencies** (the last row must be 1.0):

```
η_ch(x) = η_ch^base · m_ch(x)         η_dis(x) = η_dis^base · m_dis(x)
```

Per-unit losses are then

```
ℓ_ch(x) = x · (1/η_ch(x) − 1)         ℓ_dis(x) = x · (1 − η_dis(x))
```

and the loader verifies `ℓ` is non-negative, non-decreasing and **convex**, then stores its
piecewise-linear segments as slopes `a_g` and intercepts `b_g`.

**Direction of the effect — note carefully.** Because the multiplier scales efficiency *upward*, a
multiplier above 1 means *better* efficiency. The shipped defaults are

| `relative_power_pu` | 0.05 | 0.10 | 0.25 | 0.50 | 0.75 | 1.00 |
|---|---|---|---|---|---|---|
| charge multiplier | 1.0368 | 1.0332 | 1.0263 | 1.0168 | 1.0074 | 1.0000 |
| discharge multiplier | 1.0302 | 1.0271 | 1.0219 | 1.0135 | 1.0052 | 1.0000 |

which give charge efficiency 0.933 at 5 % power falling to 0.900 at full power, i.e. **marginal
loss rises from 7.2 % to 13.6 % of throughput as power rises**. That is the physically correct
ohmic (`I²R`) behaviour: loss per unit of energy grows with current. Convexity is what makes the
epigraph in §4.3 exact.

---

## 4. The optimisation model

### 4.1 Availability and replacement masks

Let `A_{y,k} ∈ {0,1}` be the **active** mask (cohort `k` is in service in year `y`) and
`M_{y,k} ∈ {0,1}` the **commissioning** mask (cohort `k` is installed or replaced in year `y`). Both
are built from `L` by `replacement_active_mask` and `replacement_commission_mask`: commissioning
occurs in years `1, 1+L, 1+2L, …`. Nameplate availability is

```
E_nom,y,k = U_k · E_unit · A_{y,k}                              (C1)
```

In coefficients mode **no exogenous degradation rate is applied here** — calendar fade is carried
explicitly in the state (§4.4), and applying `r_cal` to the ceiling as well would count it twice.

### 4.2 Power limits

```
P_ch  ≤ P_inv · A                                                (C2)
P_dis ≤ P_inv · A                                                (C3)
P_ch^dc  ≤ P_inv · A                                             (C4)
P_dis^dc ≤ P_inv · A                                             (C5)
P_inv ≤ ρ_ch  · U · E_unit                                       (C6)
P_inv ≤ ρ_dis · U · E_unit                                       (C7)
Σ_k U_k · E_unit ≤ E_max                                         (C8)
```

(C4)–(C5) exist only with the epigraph; with constant efficiency the DC flows are fixed multiples
of the AC flows, so (C2)–(C3) already bound them.

**Note:** (C6)–(C7) tie inverter power to **nameplate**, not to the faded state. Power capability
therefore does not degrade — see limitation §9.5.

### 4.3 AC/DC coupling: constant efficiency (default) or convex loss epigraph

Every constraint below that uses `P_ch^dc` or `P_dis^dc` — the cycle fade (D4) and the state of
charge (C13), (C15) — is written on the energy entering and leaving the cells. The loss model only
decides how those two flows relate to the AC-side variables.

**Constant efficiency** (`loss_model = constant_efficiency`, SSA pipeline default):

```
P_ch^dc  = η_ch · P_ch                                          (C9')
P_dis^dc = P_dis / η_dis                                        (C10')
```

These are substituted as linear expressions, so the model has no DC variables, no loss variables
and no (C4)–(C5), (C11)–(C12). With degradation off this is exactly the September 2026 battery
model. With degradation on, the ageing layer (§4.4) is the only addition.

Why it is the default: on four Ethiopian clusters at production settings (Gurobi 1 thread,
barrier + crossover, 8760 h × 20 y) the epigraph made the model about 3× larger in constraints and
non-zeros, the solve 10–20× slower (829–2,479 s against 73–121 s) and peak memory about 2.5×
(5.3–6.5 GB against 2.3 GB). That is not affordable for ~410,000 SSA clusters. The power
dependence of efficiency is expected to be a second-order effect on sizing and cost; the same
four clusters, solved with both loss models, are the check.

**Convex loss epigraph** (`loss_model = convex_loss_epigraph`):

```
P_ch  = P_ch^dc  + L_ch                                          (C9)
P_dis = P_dis^dc − L_dis                                         (C10)
L_ch  ≥ a_g^ch  · P_ch^dc  + b_g^ch  · (P_inv·A)      ∀g         (C11)
L_dis ≥ a_g^dis · P_dis^dc + b_g^dis · (P_inv·A)      ∀g         (C12)
```

(C11)–(C12) are the epigraph of the convex loss curve; at the optimum the binding segment is the
correct one, so no binaries are needed. `L_ch, L_dis ≥ 0` are the variable bounds.

### 4.4 The degradation state — the core of the module

```
E_{y,s,k} ≤ E_nom,y,k                                            (D1)
E_{y,s,k} ≥ SoH_eol · E_nom,y,k                                  (D2)   ← end-of-life floor
E_{1,s,k} = SoH₀ · E_nom,1,k                                     (D3)
F^cyc_{y,s,k} = Σ_t c(T_t) · P_dis^dc_{t,y,s,k}                  (D4)
F^cal_{y,k}   = r_cal,y · E_nom,y,k                              (D5)
E_{y} = [E_{y−1} − F^cyc_{y−1} − F^cal_{y−1}]
        + M_{y}·( SoH₀·E_nom,y − [E_{y−1} − F^cyc_{y−1} − F^cal_{y−1}] )   (D6)
```

(D6) is an **equality**: in a normal year the state carries forward minus both fades; in a
commissioning year it resets to a fresh cohort. (D1) clips inactive cohorts to zero. (D2) is the
constraint that makes the rated lifetime binding rather than advisory.

### 4.5 State of charge

```
S_{t+1} = S_t + P_ch^dc_t − P_dis^dc_t              t = 1…8759    (C13)
S_{1,1} = σ₀ · E_{1}                                             (C14)
S_{1,y} = S_{8760,y−1} + P_ch^dc_{8760,y−1} − P_dis^dc_{8760,y−1}
          + M_y·( σ₀·E_y − that )                                (C15)
(1 − δ) · E_{y,s,k} ≤ S_{t,y,s,k} ≤ E_{y,s,k}                    (C16)
```

(C16) is the link that makes ageing bite: **the usable window rides on the degraded state `E`, not
on nameplate.**

### 4.6 System energy balance

```
Σ_r G_r,t + Σ_k (P_dis − P_ch)_{t,k} + LL_t = D_t                (C17)
```

with `G` renewable generation, `LL` lost load (capped at `max_lost_load_fraction`, 0 by default)
and `D` demand.

### 4.7 Objective — net present cost

```
CRF(i, n) = i / (1 − (1+i)^(−n))

A^E   = U · E_unit · C_E · CRF(i, L)              annuity on battery energy
A^P   = P_inv · C_P · CRF(i, L_inv)               annuity on battery inverter
OM_y  = U · E_unit · C_E · f_om · A_y             fixed O&M on energy
OM^P_y = P_inv · C_P · f_om^inv · A_y             fixed O&M on inverter (0 by default)

NPC = Σ_y d_y · [ Σ_k (A^E + A^P)·A_{y,k}
                  + Σ_s w_s ·( OM_y + OM^P_y + PV terms + fuel
                               + externalities + lost-load cost ) ]
      + ε·throughput − ε'·Σ E                     (tie-breakers, ε = 1e-6, ε' = 1e-9)
```

`d_y` is the social discount factor. Only the battery terms are written out above; the PV,
generator and grid terms are unchanged by this module. `f_om^inv`
(`inverter_fixed_om_share_per_year`) defaults to 0. The two tie-breakers are numerical only: `ε`
discourages simultaneous charge and discharge, `ε'` keeps `E` at its largest feasible value when the
LP is otherwise indifferent. Neither is a degradation cost.

### 4.8 What is priced and what is not — read this table twice

| In the objective | magnitude at defaults |
|---|---|
| battery energy annuity | `650 · CRF(0.10, 7) = 133.5` €/kWh/yr, every active year |
| battery inverter annuity | `300 · CRF(0.10, 8)` €/kW/yr |
| fixed O&M | 3.85 % of energy CAPEX per year |

| **Not** in the objective |
|---|
| `F^cyc` — cycle fade carries **no price at all** |
| `F^cal` — calendar fade carries **no price at all** |
| `E` / SoH — no price, beyond a 1e-9 numerical tie-break |

**Therefore: cycling one extra kWh is free in cash terms.** Degradation costs money only
*indirectly* — it shrinks `E` through (D6), the usable window shrinks through (C16), demand must
still be met through (C17), so more nameplate must be installed, and nameplate is priced. One
channel, no double counting.

This is deliberate. An earlier version also charged a wear price through an epigraph
`Z ≥ max(calendar annuity, c_repl·φ·F^cyc)`. That was removed because (a) it bills the same physics
twice — once as lost capacity, once as cash — and (b) measured on a solved run its cycle branch was
slack in **every** year, so the wear price was identically zero and the variable merely reproduced
the annuity it sat beside.

---

## 5. Economic lifetime

### 5.1 `calendar_lifetime_years` does three separate jobs

1. **Replacement timing** — sets `M` and `A`, hence when a fresh cohort arrives and SoH resets.
2. **Amortisation period** — `CRF(i, L)` in §4.7, so it directly scales the annual capital charge.
   `CRF(0.10, 7) = 0.2054` versus `CRF(0.10, 8) = 0.1874`: a one-year difference moves the charge
   from 133.5 to 121.8 €/kWh/yr, **9.6 %**.
3. **Linearisation window** — the horizon `L` in the `r_cal` expression of §3.2.

Nothing inside the LP connects `L` to `SoH_eol`. They meet in exactly one place: the input-time
guard of §6.2. So left to itself `L` is free to contradict the physics, and before the calendar
recalibration it did — retiring cells at SoH 0.94 with two thirds of their life unused.

### 5.2 Two legitimate meanings, one field

- **Physical service life** — when SoH actually reaches `SoH_eol`. Must be consistent with the
  physics; should be *derived*.
- **Replacement policy** — warranty term, O&M contract, financing tenor. Legitimately exogenous and
  legitimately *shorter* than the physical life.

The field currently serves both at once, and also sets the CRF. The model cannot express a policy
replacement date and a separate physical trajectory simultaneously — see limitation §9.8.

### 5.3 Derived service life (the default)

Leave `calendar_lifetime_years: null` and `L` is derived per cluster as the root of

```
φ_cal·λ_cal·Q_cal^raw(SoC_mean, T̄_cell, 365·L)  +  L · f^cyc  =  B          (E1)
```

solved by bisection (the left side is strictly increasing in `L`, so the root is unique), then
**floored to a whole year** because the masks step in integers. Flooring keeps the cohort inside its
budget, so a derived life can never trip the §6.2 guard.

`f^cyc` is the annual cycle fade as a fraction of nameplate — the one term that is a dispatch
decision, so it must be assumed. It is estimated from the load and irradiance profiles:

```
dark hours        : H = { t : Σ_r availability_{r,t} ≤ 10⁻⁹ }
annual dark load  : Λ = Σ_{t∈H} D_t
design night      : Δ = 95th percentile of daily dark-hour load
cycles per year   : n_EFC = Λ · SoH_eol / Δ                                   (E2)
weighted coeff.   : c̄ = Σ_{t∈H} c(T_t)·D_t / Σ_{t∈H} D_t                      (E3)
cycle fade        : f^cyc = c̄ · n_EFC · δ                                     (E4)
```

**Why (E2) works.** An off-grid battery sized to carry the design night is discharged every night,
so cycles per year ≈ (annual dark load)/(design night) — **the absolute sizing cancels**, which is
exactly what makes this computable before the LP runs. The `SoH_eol` factor accounts for the
battery having to carry that same night while degraded to its end-of-life state, which is the
condition a planner actually sizes for; omitting it understates nameplate and so overstates cycling
by roughly `1/SoH_eol`.

**(E3)** weights the fade coefficient toward the hours discharge actually happens in — the cool
ones — rather than averaging flat over the year.

**Validation** against a solved LP on the BDI cluster, which the estimator never sees:

| quantity | estimated | realised | error |
|---|---|---|---|
| equivalent full cycles/yr | 271.3 | ≈ 265 | 2.4 % |
| cycle fade | 1.427 %/yr | 1.394 %/yr | 2.4 % |
| **service life** | **7.32 y** | **7.57 y** | **3.3 %** |

Across sites (same load shape, varying temperature):

| mean cell T | 20 °C | 25 °C | 30 °C | 35 °C | 40 °C | 45 °C | 50 °C |
|---|---|---|---|---|---|---|---|
| derived `L` | 16.2 y | 12.7 y | 9.8 y | 7.5 y | 5.7 y | 4.3 y | 3.2 y |
| used (floored) | 16 | 12 | 9 | 7 | 5 | 4 | 3 |

Note the old hard-coded 8 years is correct at roughly 34 °C cell and wrong by a factor of two at
both ends of the SSA range.

**Traceability.** Every derived run records, in
`settings.battery_model.degradation_model`: `calendar_lifetime_mode`,
`calendar_lifetime_years_derived`, `calendar_lifetime_years_used`,
`assumed_equivalent_full_cycles_per_year`, `assumed_cycle_fade_per_year`,
`discharge_weighted_cycle_fade_coefficient`, `mean_cell_temperature_c`,
`calendar_fade_budget_fraction`, and `calendar_fade_warning` when applicable.

**Flooring costs something.** 7.32 → 7 retires the cohort slightly early: on the reference cluster
it ends at SoH 0.834 with 17 % of the budget unused, and the steeper CRF accounts for about 3 of the
4.6 % NPC difference against a hand-set 8 years. The CRF is deliberately held on the *same* integer
as the replacement mask: amortising over 7.32 while replacing every 7 years would under-recover
capital. Rounding to nearest instead of flooring is a defensible one-line alternative now that the
floor (D2) exists, since overshooting the budget forces oversizing rather than infeasibility.

### 5.4 Consistency diagnostic — always run this

Look at SoH in the year **before** a replacement:

| observed | verdict |
|---|---|
| ≈ `SoH_eol` (0.80–0.85) | interval matches the physics |
| ≫ `SoH_eol` (e.g. 0.94) | interval too short — scrapping healthy cells, overpaying CAPEX |
| guard fires | interval too long — see §6.2 |

---

## 6. Guards against degenerate inputs

Two distinct failure modes can make a physically impossible site look like a valid run. Each is
refused where it arises.

### 6.1 Sub-two-year life — `implied_calendar_life` raises instead of clamping

The bisection in (E1) needs the root inside its bracket. If the battery is already dead before the
lower bound, there is no root. The old code **clamped** and returned the bound; it now **raises**.

Clamping to `L = 1` is the most destructive value available, because:

- every year becomes a commissioning year, so SoH resets annually;
- fade therefore never accumulates and the degradation state **silently switches itself off**;
- the end-of-life floor (D2) is trivially satisfied at SoH 1.00;
- the §6.2 guard tests `r_cal·(L−1) = 0`, so it goes blind precisely where it is needed;
- `CRF(0.10, 1) = 1.10`, i.e. 715 €/kWh/yr — a new battery every year at full price.

The result would be a feasible, optimal-looking, expensive solution in which the battery never ages,
at a site where the real cell dies in months. Two hard errors replace it:

1. fade in the **first year alone ≥ B** — no replacement interval can represent that;
2. implied life **< 2 years** — below the resolution of an annual replacement model.

Both name the cell temperature and `calendar_fade_scale`. Reachable at roughly 48 °C cell with
`φ_cal = 2.0`, or 55 °C at `φ_cal = 2.7`.

### 6.2 End-of-life floor feasibility

The binding year is a cohort's last. Writing `D` for annual discharge throughput:

```
E_nom · [ B − r_cal·(L−1) ]  ≥  c̄ · D · (L−1)                               (G1)
```

`D` has a hard lower bound — dark-hour load must come from the battery when lost load is disallowed.
Calendar fade and the floor **both scale with nameplate**, so the bracket on the left does not
improve by buying more battery: the usual escape hatch is closed. Hence

```
LP infeasible  ⟺  r_cal·(L−1) ≥ B
```

which is exactly the guard's test, and just inside the boundary the required nameplate grows as
`1/[B − r_cal(L−1)]` — hyperbolically. A **derived** `L` can never reach this (it solves
fade(L) = B, so `r_cal·(L−1) < B` by construction; measured values sit at 0.26–0.40 of `B` across
20–60 °C). The check is therefore the validator for a **hand-set** `L`, and the error message names
the appropriate lever for whichever mode is active.

### 6.3 Warning band

Above **70 %** of the budget the run stays feasible but sizing becomes hypersensitive to the
enclosure and calendar assumptions. It emits a `UserWarning` and records
`calendar_fade_warning`. `calendar_fade_budget_fraction` is recorded for *every* run, so a
thousand-cluster sweep can be sorted on it.

Verified: a hand-set 10 y at a 45 °C cell site sits at 71 % and warns; 20 y at the same site is
refused with "lower to at most 19.1 y".

---

## 7. Complete input reference

### 7.1 Degradation (`battery.yaml → battery.technical`)

| Parameter | Default | Unit | Why it exists | How to set it |
|---|---|---|---|---|
| `chemistry` | `LFP` | – | selects both coefficient sets | `LFP` or `NMC`. Only LFP has a measured calendar anchor; NMC runs on the unscaled fit |
| `initial_soh` | 1.0 | – | starting health, (D3) | 1.0 unless modelling second-life cells |
| `end_of_life_soh` | 0.8 | – | **two jobs**: the floor (D2), and `B = SoH₀ − SoH_eol` which calibrates `λ_cyc` and the guards | the SoH your datasheet's cycle count refers to |
| `cycle_lifetime_to_eol_cycles` | 6000 | cycles | pins cycle-fade magnitude | datasheet cycle count |
| `cycle_life_reference_dod` | 0.8 | – | the DoD that count was measured at | **read off the same datasheet line** — do not leave at 0.8 if yours quotes 100 % |
| `cycle_life_reference_temperature_c` | 25 | °C | ditto for temperature | almost always 25 |
| `enclosure_temperature_rise_c` | 10 | K | outdoor air → enclosure air | 0 cooled · 5–10 ventilated · 15–20 sealed in sun. **Run as a sensitivity** |
| `calendar_fade_scale` | 1.0 | – | walks the 2.7× literature band | 1.0 anchored (central) · 2.72 unscaled Ali (pessimistic). Report both |

Plus `formulation.json → battery_model.degradation_model.coefficients_enabled` (master switch).
`battery_model.loss_model` can be `constant_efficiency` (SSA pipeline default) or
`convex_loss_epigraph`; the coefficient model works with both (§4.3). Only the legacy flat
`cycle_fade_enabled` / `calendar_fade_enabled` scheme still requires the epigraph.

### 7.2 Economics (`battery.yaml → battery.investment.by_step.*`)

| Parameter | Default | Unit | Notes |
|---|---|---|---|
| `nominal_capacity_kwh` | 1.0 | kWh | sizing granularity only |
| `specific_investment_cost_per_kwh` | 650 | €/kWh | the only energy capital charge |
| `wacc` | 0.10 | 1/yr | discount rate in `CRF` |
| `calendar_lifetime_years` | **null → derived** | yr | §5. Set a number only for a contractual policy |
| `fixed_om_share_per_year` | 0.0385 | 1/yr | fraction of energy CAPEX |
| `inverter_specific_investment_cost_per_kw` | 300 | €/kW | |
| `inverter_lifetime_years` | 8 | yr | independent of the energy life |

### 7.3 Dispatch physics (`battery.yaml → battery.technical`)

| Parameter | Default | Unit | Notes |
|---|---|---|---|
| `depth_of_discharge` | 0.8 | – | sets the usable window (C16) **and** `SoC_mean = 1 − δ/2` for calendar fade |
| `initial_soc` | 1.0 | – | (C14) |
| `max_charge_c_rate` | 0.2 | 1/h | **must be finite and positive — a null reads as zero and silently disables the battery** |
| `max_discharge_c_rate` | 0.25 | 1/h | same warning |
| `max_installable_capacity_kwh` | 1e6 | kWh | (C8) |
| `charge_efficiency` | 0.9 | – | base for the multiplier curve |
| `discharge_efficiency` | 0.9 | – | base for the multiplier curve |
| `efficiency_curve_csv` | `battery_efficiency_curve.csv` | path | §3.4 |

### 7.4 Time-series inputs (`inputs/`)

| File | Content | Source |
|---|---|---|
| `ambient_temperature.csv` | hourly outdoor air temperature | PVGIS, downloaded with irradiance. **The key per-cluster degradation input** |
| `resource_availability.csv` | hourly irradiance/availability | PVGIS |
| `load_demand.csv` | hourly demand | project |
| `battery_efficiency_curve.csv` | conversion-loss multipliers | shipped default, or inverter test data |

---

## 8. Assumptions, stated explicitly

1. **Throughput-limited cycling.** Fade depends on total kWh discharged, not on the depth of
   individual cycles (depth effect is 1.7 % in the data). Defensible for LFP; weaker for NMC.
2. **Discharge-only accounting.** `F^cyc` counts discharge only, so each cycle is counted once.
3. **Additive ageing.** Calendar and cycle fade add linearly. Real mechanisms couple through shared
   SEI growth; additivity is the standard engineering simplification.
4. **Linearised calendar rate.** A flat annual rate reproduces cumulative loss at `L` but
   straightens the true `τ^0.48` trajectory within a cohort's life.
5. **Fixed mean SoC.** Calendar fade uses `1 − δ/2`, not the realised SoC trajectory (which would
   make the term bilinear and the model non-linear).
6. **Annual-mean temperature for calendar fade, hourly for cycle fade.** Calendar ageing is slow;
   cycle ageing is attributed to the hour it occurs in. Using the annual mean under-states calendar
   fade slightly by Jensen's inequality (≈ 2.4 % for a ±8 °C diurnal swing).
7. **Instantaneous ambient drives cycle fade.** No cell thermal inertia. A real 280 Ah cell smooths
   the diurnal swing over hours, so the hourly coefficient overstates the intraday spread (137 % on
   raw hourly ambient against 26 % on daily means) and gives the optimiser a sharper
   "discharge when cool" incentive than physics warrants.
8. **Fixed c-rate in the fitted curve.** `Ψ` was distilled at 0.4 C; the configuration permits at
   most 0.25 C discharge, so the shipped self-heating is conservative.
9. **Exogenous replacement schedule.** Replacement is on a fixed interval; the LP cannot choose to
   replace early. Endogenous timing requires integers.
10. **One cohort geometry.** `SoH` resets to `SoH₀` at commissioning; no partial refurbishment, no
    mixed-age strings.
11. **Enclosure rise is a constant offset**, identical in every hour and season.
12. **Dark-hour load proxy for throughput** in (E2): all dark-hour demand is served by the battery,
    and daytime battery cycling is negligible.
13. **Nameplate-referenced fades.** Both fades are fractions of nameplate, not of current capacity,
    which keeps (D4)–(D6) linear.

## 9. Limitations, stated explicitly

1. **Not an absolute lifetime predictor.** Every significant error points the same way —
   optimistic. Treat it as a relative instrument for ranking sites.
2. **The enclosure rise dominates everything** and is a judgement call: 0 → 20 K moves the derived
   life from 12.7 to 4.3 years at a 25 °C ambient site. Deriving `L` does not remove this
   uncertainty; it *propagates* it into the replacement schedule and hence into cost. This should be
   the headline sensitivity of any study using the module.
3. **Calendar magnitude is uncertain by 2.7×**, which is the honest spread between the cross-study
   fit and the one long-duration measurement available.
4. **Temperature sensitivity may be understated.** The cycle curve doubles per 16.7 °C; standard
   Arrhenius (doubling per 10 °C) would give 11.3× over 10–45 °C against the model's 4.28×.
5. **Power capability does not degrade.** (C6)–(C7) reference nameplate, so an aged battery is
   weaker in energy but just as strong in power. Real cells lose both through resistance growth.
6. **No round-trip efficiency degradation.** Internal resistance growth is not modelled, so
   conversion losses are constant over life. In the SSA default they are also constant with
   power (`η_ch = η_dis = 0.9` at any C-rate, §4.3).
7. **No SoC-dependent calendar fade in the LP.** A PV battery sits near full charge for much of the
   dry season, which the fixed `1 − δ/2` under-represents.
8. **One field for two concepts.** `calendar_lifetime_years` cannot express a contractual
   replacement date and a separate physical trajectory at the same time.
9. **No terminal-SOC constraint.** The final year's last-hour discharge enters no balance, so it is
   effectively free energy — about 8 kWh once in 20 years on the reference cluster. Negligible in
   magnitude but a genuine unforced relaxation (upstream has `soc_terminal_upper/lower`).
10. **Non-cell failures are not modelled at all** — BMS faults, inverter failures, installation
    quality, over-discharge events, dust, lightning. In SSA field experience these frequently set
    replacement timing more than chemistry does.
11. **Integer flooring of the derived life** biases toward early retirement, by up to one year.
12. **`battery_cycle_fade_coefficient` is stored per (period, year, scenario)**, duplicating a
    single typical year 20× when the temperature series does not vary by year.
13. **20 dense LP rows.** Each annual `F^cyc` row in (D4) has 8760 non-zeros, which slows the
    barrier factorisation (193 s against 145 s for the same-sized model with no degradation).

---

## 10. Worked example — reference cluster

BDI cluster 1, 8760 h × 20 y, one scenario, PV + LFP battery, no generator, zero lost load.
Gurobi 13 barrier, 8 threads, `Method=2 Crossover=0`. Computed with `loss_model =
convex_loss_epigraph`, before constant efficiency became the pipeline default (§4.3).

**Inputs.** 25 °C mean ambient, +10 K enclosure → **35 °C mean cell**. LFP, 6000 cycles @ 80 % DoD /
25 °C, `SoH₀ = 1.0`, `SoH_eol = 0.8`, `φ_cal = 1.0`, `calendar_lifetime_years: null`.

**Derived lifetime.**

```
equivalent full cycles/yr        271.3   (0.74/day)
discharge-weighted c             65.72e-6 per kWh
assumed cycle fade               1.427 %/yr
derived service life             7.3178 y  →  used 7 y
commissioning years              1, 8, 15
calendar fade budget fraction    0.40  (no warning; threshold 0.70)
```

**Solution.**

| quantity | value |
|---|---|
| battery nameplate | 52.7879 kWh |
| battery inverter | 8.0434 kW |
| PV | 6.4016 units |
| cycle fade | 0.7538 kWh/yr = 1.428 %/yr |
| calendar fade | 0.7058 kWh/yr = 1.337 %/yr |
| total fade | 1.4596 kWh/yr = 2.765 %/yr |
| usable energy, years 1→7 | 52.788 → 51.328 → 49.868 → 48.408 → 46.948 → 45.487 → 44.026 |
| SoH at retirement | 0.834 |
| SoH budget consumed | 83 % |
| **NPC** | **83 005.64** |

Calendar fade is now slightly **larger** than cycle fade — 48 % versus 52 % of the damage. Under the
upstream coefficients calendar fade was 6 % of the total and had no effect on any output.

**Model size.** 1 576 864 variables, 4 204 925 constraints; barrier 193 s; Cholesky factor 2.0 GB.
Against an otherwise identical model with degradation disabled (1 576 804 / 4 204 824), the whole
degradation layer costs **60 variables and 101 constraints**.

**Cumulative effect of the module's development** on this cluster:

| configuration | battery | fade/yr | SoH at retirement | budget used | NPC |
|---|---|---|---|---|---|
| no degradation | 44.10 kWh | — | — | — | 66 909.62 |
| SOC bands + wear epigraph (upstream scheme) | 46.86 kWh | 0.86 % | 0.940 | 32 % | 70 350.42 |
| additive fade + EoL floor, bands dropped | 47.03 kWh | 0.90 % | 0.937 | 32 % | 70 568.66 |
| + calendar recalibration + enclosure rise | 52.98 kWh | 2.41 % | 0.832 | 84 % | 78 002.53 |
| + cycle-life calibration fix | 54.06 kWh | 2.64 % | 0.815 | 93 % | 79 351.87 |
| **+ derived service life (current)** | **52.79 kWh** | **2.77 %** | **0.834** | **83 %** | **83 005.64** |

Degradation now moves NPC by **+24.1 %** against a no-degradation baseline, against +5.1 % under the
upstream scheme — and the lifetime, the fade rates and the replacement schedule are mutually
consistent rather than three independent guesses.

**The invariant worth noticing.** Usable energy at retirement is 44.03–44.06 kWh in every one of
these runs. That number is fixed by the dispatch requirement and is completely insensitive to the
degradation assumptions. Everything the module does is decide **how much nameplate you must buy to
still have 44 kWh usable when the battery is retired** — 46.9 kWh under the upstream physics,
52.8 kWh under the measured physics. A 13 % difference in procurement.

**Temperature is the whole story.** On an otherwise identical cluster, moving from a 25 °C to a
40 °C mean ambient (with a 20-year calendar life, so one cohort spans the horizon) changes battery
sizing by +54 % and NPC by +43 %, with the end-of-life floor binding to within 0.03 % at the hot
site and slack at the cool one.

---

## 11. Divergences from upstream MicroGridsPy

| Area | Upstream | Here | Why |
|---|---|---|---|
| depth resolution | `n_soc_bands` SOC-band LP states | single `c(T)` coefficient | depth effect is 1.7 %; bands cost 1.58 M variables and changed the optimum by 0.007 % |
| wear cost | `Z ≥ max(calendar annuity, c_repl·φ·F^cyc)` epigraph | deleted | double counts; its cycle branch was slack in every year, so the charge was identically zero |
| combining the two fades | capacity took a `min`, cost took a `max` | additive in the state | they add physically |
| end-of-life | `SoH_eol` used only in the cost term | hard floor (D2) | makes the rated lifetime binding rather than advisory |
| calendar coefficients | hard-coded per-hour cubic, 0.052 %/yr at 25 °C (386-year life); also disagrees ~6× with its own JSON `alpha_poly` | Ali et al. (2023) fit anchored to a measured 10-year shelf test, 0.85 %/yr | upstream value is 1–2 orders of magnitude below every measured LFP dataset |
| temperature input | ambient air | cell = ambient + `enclosure_temperature_rise_c` | cells run 10–20 K above outdoor air in SSA enclosures |
| calendar SoC | step at `δ ≥ 0.75` | `1 − δ/2` | the step jumped fade 30 % between δ 0.74 and 0.76 |
| cycle-life scaling | `N_ref/N_rated` with hard-coded `N_ref` | `B/(N_rated·Ψ(δ_ref,T_ref))` | upstream delivered 7372 cycles for a requested 6000 at 80 % DoD |
| service life | user-set scalar | derived from the physics by default | reconciles `calendar_lifetime_years` with `end_of_life_soh` |

Not ported from upstream: terminal-SOC constraints (`soc_terminal_upper/lower`) — see limitation
§9.9.

---

## 12. References

1. Ali, M. U. et al. (2023). *Assessment of the calendar aging of lithium-ion batteries for a
   long-term — space missions.* **Frontiers in Energy Research** 11:1108269. Table 3 supplies the
   calendar-fade shape coefficients.
   <https://www.frontiersin.org/journals/energy-research/articles/10.3389/fenrg.2023.1108269/full>
2. *Shelf life of lithium-ion batteries: Recommissioning LiFePO₄/C cells after ten years of
   uninterrupted calendar aging.* **Journal of Power Sources** (2025), S0378775325016155. Supplies
   the magnitude anchor: 96–98 % retention after 10 y at 6 °C and 50 % SoC.
   <https://www.sciencedirect.com/science/article/pii/S0378775325016155>
3. Naumann, M. et al. (2018). *Analysis and modeling of calendar aging of a commercial
   LiFePO₄/graphite cell.* **Journal of Energy Storage**. 885-day, 17-point LFP storage study; one of
   the datasets behind reference 1, and the source of the ~2× SoC sensitivity cross-check.
   <https://www.sciencedirect.com/science/article/abs/pii/S2352152X18300665>
4. `engines/new/core/data_pipeline/layer1_liion_coefficients.json` — cycle-fade shape `Ψ(D,T)`,
   distilled from an offline Layer-I electro-thermal model; carries its own provenance block
   (`protocol_sha1`, cell parameters, thermal protocol).

## 13. Where the code lives

| Concern | File |
|---|---|
| coefficient curves, calibration, derived life | `engines/new/core/data_pipeline/battery_degradation_coefficients.py` |
| conversion-loss curve parsing | `engines/new/core/data_pipeline/battery_loss_model.py` |
| settings parsing / validation | `engines/new/core/data_pipeline/battery_degradation_model.py` |
| input assembly, cell temperature, guards | `engines/new/core/multi_year_model/data.py` |
| LP variables | `engines/new/core/multi_year_model/variables.py` |
| LP constraints (C1–C17, D1–D6) | `engines/new/core/multi_year_model/constraints.py` |
| objective | `engines/new/core/multi_year_model/objective.py` |
| pipeline overview | `docs/PIPELINE.md` §4b–4e |
