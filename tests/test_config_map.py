"""Battery defaults written into formulation.json by the pipeline.

These two settings decide model size (loss model) and the derived battery life (cycling
estimate) for every cluster, so a silent change of default should fail a test.

Run from the repo root:  python -m pytest tests/test_config_map.py -q
"""
from __future__ import annotations

from mgpy2.config_map import ThesisConfig, build_formulation_payload


def test_battery_defaults():
    battery = build_formulation_payload("x", ThesisConfig())["battery_model"]
    assert battery["loss_model"] == "constant_efficiency"
    assert battery["degradation_model"]["coefficients_enabled"] is True
    assert battery["degradation_model"]["cycling_estimate"] == "horizon_mean"


def test_battery_options_pass_through():
    cfg = ThesisConfig()
    cfg.battery_loss_model = "convex_loss_epigraph"
    cfg.battery_cycling_estimate = "first_year"
    battery = build_formulation_payload("x", cfg)["battery_model"]
    assert battery["loss_model"] == "convex_loss_epigraph"
    assert battery["degradation_model"]["cycling_estimate"] == "first_year"


def test_two_investment_steps_by_default():
    """D02 (9 Oct 2026): build in year 1 and year 11 of the 20-year horizon."""
    form = build_formulation_payload("x", ThesisConfig())
    assert form["capacity_expansion"] is True
    assert form["investment_steps_years"] == [10, 10]


def test_step_durations_must_cover_the_horizon():
    import pytest
    cfg = ThesisConfig()
    cfg.investment_step_years = (10, 5)
    with pytest.raises(ValueError, match="sum to the 20-year horizon"):
        cfg.investment_steps_years()
    cfg.capacity_expansion = False                 # one step: durations are ignored
    assert cfg.investment_steps_years() is None
