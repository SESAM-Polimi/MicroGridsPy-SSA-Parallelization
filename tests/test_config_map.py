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
