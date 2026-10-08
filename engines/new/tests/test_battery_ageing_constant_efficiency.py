"""Battery ageing (coefficient model) with the constant-efficiency loss model.

A three-year toy system (4 hours per year: two night hours of load, two sunny hours) is small
enough for HiGHS to solve in well under a second, and still exercises everything the ageing
layer does: fade definitions, the year-to-year state, the replacement reset, the end-of-life
floor and the SoC window riding on the faded capacity.

Run from engines/new:  python -m pytest tests/test_battery_ageing_constant_efficiency.py -q
"""
from __future__ import annotations

import linopy as lp
import numpy as np
import pytest
import xarray as xr

from core.multi_year_model.constraints import initialize_constraints
from core.multi_year_model.objective import initialize_objective
from core.multi_year_model.variables import initialize_vars

YEARS = [2026, 2027, 2028]
PERIODS = 4
ETA_C, ETA_D = 0.9, 0.95       # different on purpose: a swapped efficiency would show up
CYCLE_FADE_C = 0.01            # kWh of capacity lost per kWh leaving the cells
CALENDAR_RATE = 0.02           # fraction of nameplate lost per year
LIFETIME_Y = 2.0               # cohorts commissioned in 2026 and 2028
SOH0, SOH_EOL, DOD = 1.0, 0.7, 0.8
TOL = 1e-6


def _sets() -> xr.Dataset:
    """Same layout as core.multi_year_model.sets.initialize_sets, one investment step."""
    ds = xr.Dataset(
        coords={
            "period": ("period", list(range(PERIODS))),
            "year": ("year", YEARS),
            "inv_step": ("inv_step", [1]),
            "scenario": ("scenario", ["scenario_1"]),
            "resource": ("resource", ["Solar"]),
        }
    )
    ds["inv_step_start_year"] = xr.DataArray([YEARS[0]], dims=("inv_step",))
    ds["inv_step_end_year"] = xr.DataArray([YEARS[-1]], dims=("inv_step",))
    ds["inv_step_len_years"] = xr.DataArray([len(YEARS)], dims=("inv_step",))
    ds["year_inv_step"] = xr.DataArray([1] * len(YEARS), dims=("year",))
    ds["inv_active_in_year"] = xr.DataArray([[1] * len(YEARS)], dims=("inv_step", "year"))
    return ds


def _data(sets: xr.Dataset, *, coefficients: bool, cycle_c: float = CYCLE_FADE_C,
          calendar_rate: float = CALENDAR_RATE, legacy_cycle_fade: bool = False) -> xr.Dataset:
    y, t, s, r, k = (sets.coords[n] for n in ("year", "period", "scenario", "resource", "inv_step"))

    def per_step(v):
        return xr.DataArray([v], dims=("inv_step",), coords={"inv_step": k})

    def per_step_res(v):
        return xr.DataArray([[v]], dims=("inv_step", "resource"), coords={"inv_step": k, "resource": r})

    def per_res(v):
        return xr.DataArray([v], dims=("resource",), coords={"resource": r})

    night_load = np.array([1.0, 1.0, 0.0, 0.0])
    sun = np.array([0.0, 0.0, 1.0, 1.0])
    data = xr.Dataset({
        "load_demand": xr.DataArray(np.tile(night_load[None, :, None], (len(YEARS), 1, 1)),
                                    dims=("year", "period", "scenario"), coords={"year": y, "period": t, "scenario": s}),
        "resource_availability": xr.DataArray(np.tile(sun[None, :, None, None], (len(YEARS), 1, 1, 1)),
                                              dims=("year", "period", "scenario", "resource"),
                                              coords={"year": y, "period": t, "scenario": s, "resource": r}),
        "scenario_weight": xr.DataArray([1.0], dims=("scenario",), coords={"scenario": s}),
        "min_renewable_penetration": xr.DataArray(0.0),
        "max_lost_load_fraction": xr.DataArray(0.0),
        "lost_load_cost_per_kwh": xr.DataArray(1.0e6),
        "land_availability_m2": xr.DataArray(np.nan),
        "emission_cost_per_kgco2e": xr.DataArray(0.0),
        "res_nominal_capacity_kw": per_step_res(1.0),
        "res_lifetime_years": per_step_res(20.0),
        "res_specific_investment_cost_per_kw": per_step_res(50.0),
        "res_inverter_specific_investment_cost_per_kw_ac": per_step_res(25.0),
        "res_inverter_lifetime_years": per_step_res(15.0),
        "res_wacc": per_step_res(0.05),
        "res_grant_share_of_capex": per_step_res(0.0),
        "res_embedded_emissions_kgco2e_per_kw": per_step_res(0.0),
        "res_fixed_om_share_per_year": per_step_res(0.0),
        "res_inverter_fixed_om_share_per_year": per_step_res(0.0),
        "res_production_subsidy_per_kwh": per_step_res(0.0),
        "res_dc_ac_ratio": per_res(1.0),
        "res_inverter_efficiency": per_res(1.0),
        "res_specific_area_m2_per_kw": per_res(0.0),
        "res_max_installable_capacity_kw": per_res(np.nan),
        "res_capacity_degradation_rate_per_year": per_res(0.0),
        "battery_nominal_capacity_kwh": per_step(1.0),
        "battery_specific_investment_cost_per_kwh": per_step(100.0),
        "battery_inverter_specific_investment_cost_per_kw": per_step(1.0),
        "battery_inverter_lifetime_years": per_step(12.0),
        "battery_wacc": per_step(0.05),
        "battery_calendar_lifetime_years": per_step(LIFETIME_Y),
        "battery_fixed_om_share_per_year": per_step(0.0),
        "battery_inverter_fixed_om_share_per_year": per_step(0.0),
        "battery_embedded_emissions_kgco2e_per_kwh": per_step(0.0),
        "battery_max_installable_capacity_kwh": xr.DataArray(np.nan),
        "battery_charge_efficiency": xr.DataArray(ETA_C),
        "battery_discharge_efficiency": xr.DataArray(ETA_D),
        "battery_initial_soc": xr.DataArray(1.0),
        "battery_initial_soh": xr.DataArray(SOH0),
        "battery_end_of_life_soh": xr.DataArray(SOH_EOL),
        "battery_depth_of_discharge": xr.DataArray(DOD),
        "battery_max_charge_c_rate": xr.DataArray(1.0),
        "battery_max_discharge_c_rate": xr.DataArray(1.0),
        "battery_cycle_fade_coefficient_per_kwh_throughput": xr.DataArray(0.0),
        "battery_calendar_time_increment_per_year": xr.DataArray(1.0),
        "battery_capacity_degradation_rate_per_year": xr.DataArray(0.0),
        "generator_nominal_capacity_kw": per_step(1.0),
        "generator_max_installable_capacity_kw": xr.DataArray(0.0),
        "generator_nominal_efficiency_full_load": xr.DataArray(1.0),
        "generator_capacity_degradation_rate_per_year": xr.DataArray(0.0),
        "generator_specific_investment_cost_per_kw": per_step(1.0e5),
        "generator_lifetime_years": per_step(20.0),
        "generator_wacc": per_step(0.05),
        "generator_fixed_om_share_per_year": per_step(0.0),
        "generator_embedded_emissions_kgco2e_per_kw": per_step(0.0),
        "fuel_lhv_kwh_per_unit_fuel": xr.DataArray(1.0),
        "fuel_cost_per_unit_fuel": xr.DataArray(0.0),
        "fuel_fuel_cost_per_unit_fuel": xr.DataArray(0.0),
        "fuel_direct_emissions_kgco2e_per_unit_fuel": xr.DataArray(0.0),
    })
    degradation = {}
    if coefficients:
        degradation["coefficients_enabled"] = True
        data["battery_cycle_fade_coefficient"] = xr.DataArray(
            np.full((PERIODS, len(YEARS), 1), cycle_c),
            dims=("period", "year", "scenario"), coords={"period": t, "year": y, "scenario": s})
        data["battery_calendar_rate_per_year"] = xr.DataArray(
            np.full(len(YEARS), calendar_rate), dims=("year",), coords={"year": y})
    if legacy_cycle_fade:
        degradation["cycle_fade_enabled"] = True
    data.attrs["settings"] = {
        "project_name": "test_battery_ageing_constant_efficiency",
        "social_discount_rate": 0.0,
        "grid": {"on_grid": False, "allow_export": False},
        "battery_model": {"loss_model": "constant_efficiency", "degradation_model": degradation},
        "optimization_constraints": {"enforcement": "scenario_wise"},
    }
    data.attrs["conversion_technology_by_resource"] = {"Solar": "Solar PV"}
    return data


def _build(data: xr.Dataset):
    sets = _sets()
    model = lp.Model()
    v = initialize_vars(sets, data, model)
    initialize_constraints(sets, data, v, model)
    initialize_objective(sets, data, v, model)
    return model, v


def _solve(model: lp.Model) -> xr.Dataset:
    try:
        status, condition = model.solve(solver_name="highs")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"HiGHS unavailable: {exc}")
    assert status == "ok", condition
    return model.solution


def _vals(sol: xr.Dataset, name: str) -> xr.DataArray:
    """Solution of one variable, summed over the single investment step and scenario."""
    da = sol[name]
    for dim in ("inv_step", "scenario"):
        if dim in da.dims:
            da = da.sum(dim)
    return da


# ----------------------------------------------------------------------------------------------
def test_constant_efficiency_ageing_adds_only_the_yearly_state():
    """No hourly DC or loss variables; the ageing layer is three small yearly variables."""
    model, v = _build(_data(_sets(), coefficients=True))
    for name in ("battery_charge_dc", "battery_discharge_dc", "battery_charge_loss", "battery_discharge_loss"):
        assert name not in v
    for name in ("battery_cycle_fade", "battery_calendar_fade", "battery_effective_energy_capacity"):
        assert name in v
        assert "period" not in v[name].dims
    assert "battery_charge_loss_epigraph" not in model.constraints


def test_ageing_state_follows_the_physics():
    sol = _solve(_build(_data(_sets(), coefficients=True))[0])
    dis = _vals(sol, "battery_discharge")
    ch = _vals(sol, "battery_charge")
    soc = _vals(sol, "battery_soc")
    cyc = _vals(sol, "battery_cycle_fade")
    cal = _vals(sol, "battery_calendar_fade")
    eff = _vals(sol, "battery_effective_energy_capacity")
    e_nom = float(_vals(sol, "battery_units"))
    assert e_nom > 0.0

    # cycle fade is charged on the energy leaving the cells: AC discharge / eta_d
    np.testing.assert_allclose(cyc.values, CYCLE_FADE_C * (dis / ETA_D).sum("period").values, atol=TOL)
    np.testing.assert_allclose(cal.values, CALENDAR_RATE * e_nom, atol=TOL)

    # state: fresh in 2026, faded in 2027, reset by the replacement in 2028
    assert float(eff.sel(year=2026)) == pytest.approx(SOH0 * e_nom, abs=TOL)
    expected_2027 = float(eff.sel(year=2026) - cyc.sel(year=2026) - cal.sel(year=2026))
    assert float(eff.sel(year=2027)) == pytest.approx(expected_2027, abs=TOL)
    assert float(eff.sel(year=2027)) < SOH0 * e_nom - 1e-3
    assert float(eff.sel(year=2028)) == pytest.approx(SOH0 * e_nom, abs=TOL)
    assert (eff.values >= SOH_EOL * e_nom - TOL).all()

    # SoC dynamics on the DC-side flows, window riding on the faded capacity
    for year in YEARS:
        s, c, d = soc.sel(year=year).values, ch.sel(year=year).values, dis.sel(year=year).values
        np.testing.assert_allclose(s[1:], s[:-1] + ETA_C * c[:-1] - d[:-1] / ETA_D, atol=TOL)
        e = float(eff.sel(year=year))
        assert (s <= e + TOL).all() and (s >= (1.0 - DOD) * e - TOL).all()


def test_zero_fade_reproduces_the_model_without_ageing():
    """Ageing on with zero fade coefficients must give the same design and cost as ageing off."""
    sol_off = _solve(_build(_data(_sets(), coefficients=False))[0])
    model_zero, _ = _build(_data(_sets(), coefficients=True, cycle_c=0.0, calendar_rate=0.0))
    sol_zero = _solve(model_zero)
    for name in ("battery_units", "res_units", "battery_inverter_power"):
        assert float(sol_zero[name].sum()) == pytest.approx(float(sol_off[name].sum()), rel=1e-6, abs=1e-6)


def test_legacy_fade_flags_still_require_the_epigraph():
    data = _data(_sets(), coefficients=False, legacy_cycle_fade=True)
    with pytest.raises(Exception, match="convex_loss_epigraph"):
        _build(data)
