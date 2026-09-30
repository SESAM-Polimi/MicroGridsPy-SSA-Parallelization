"""
Pre-fitted Li-ion battery-degradation coefficients (inputs side only).

The cycle-fade law used by the multi-year model is a per-SOC-band marginal cost c_k(T):
capacity lost [fraction of nameplate] per unit of discharge throughput taken through
depth band k, at ambient temperature T. c_k are the slopes of the depth-resolved
per-cycle fade curve Psi(D, T) (shipped as layer1_liion_coefficients.json, distilled from
the offline Layer-I electro-thermal model): fine 10 % depth slices, each a cubic in
y = T[degC] / 10. They are re-binned here to ``n_bands`` equal usable bands over [0, DoD].

Calendar ageing is a cubic in T (alpha, fraction of nameplate per hour), reduced to one
annual rate per year from the annual-mean ambient temperature.

Shape is predefined (same for every cluster, only the temperature series changes);
magnitude is user-scalable through the rated cycle life: c_k *= N_ref / N_user.

Li-ion only (LFP, NMC). Everything here is exogenous to the LP: static coefficients.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


class InputValidationError(RuntimeError):
    pass


LFP = "LFP"
NMC = "NMC"
VALID_CHEMISTRIES = (LFP, NMC)

# alpha_hour = c1*y^3 + c2*y^2 + c3*y + c4, y = T[degC]/10; keyed by chemistry then SoC band.
_ALPHA_POLY: dict[str, dict[str, tuple[float, float, float, float]]] = {
    LFP: {
        "20": (3.446908e-10, 1.240398e-09, 1.053498e-08, 1.970248e-08),
        "40": (4.485623e-10, 1.614189e-09, 1.370967e-08, 2.563976e-08),
    },
    NMC: {
        "20": (8.323820e-11, 8.912264e-10, 6.706961e-09, 1.814235e-08),
        "40": (9.208524e-11, 9.854044e-10, 7.418433e-09, 2.006416e-08),
    },
}

# Rated cycle life at which each c_k shape was fitted.
_REFERENCE_CYCLE_LIFE: dict[str, float] = {LFP: 6000.0, NMC: 2500.0}

HOURS_PER_YEAR = 8760.0
MAX_BANDS = 10
_BAND_COEFF_PATH = Path(__file__).with_name("layer1_liion_coefficients.json")
_BAND_COEFF_CACHE: dict[str, Any] | None = None


def normalize_chemistry(raw: Any, *, default: str | None = None) -> str:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if default is not None:
            return default
        raise InputValidationError(
            f"battery.technical.chemistry is required for battery degradation. Allowed: {list(VALID_CHEMISTRIES)}."
        )
    value = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"lfp": LFP, "lifepo4": LFP, "lithium_lfp": LFP, "nmc": NMC, "nca": NMC, "lithium_nmc": NMC}
    if value not in aliases:
        raise InputValidationError(
            f"Invalid battery.technical.chemistry {raw!r}. Allowed (Li-ion only): {list(VALID_CHEMISTRIES)}."
        )
    return aliases[value]


def cycle_life_scaling(chemistry: str, user_cycle_life: float | None) -> float:
    """N_ref / N_user (1.0 when the rated cycle life is unset)."""
    n_ref = _REFERENCE_CYCLE_LIFE[normalize_chemistry(chemistry)]
    if user_cycle_life in (None, "") or not np.isfinite(float(user_cycle_life)) or float(user_cycle_life) <= 0.0:
        return 1.0
    return n_ref / float(user_cycle_life)


def _cubic(coeffs: tuple[float, float, float, float], x: np.ndarray) -> np.ndarray:
    c1, c2, c3, c4 = coeffs
    return c1 * x**3 + c2 * x**2 + c3 * x + c4


def _alpha_band_key(dod: float) -> str:
    return "20" if float(dod) >= 0.75 else "40"


def calendar_rate_per_year(chemistry: str, dod: float, mean_temperature_degc: np.ndarray) -> np.ndarray:
    """Calendar fade [fraction of nameplate per year] at the given (annual-mean) temperature."""
    chem = normalize_chemistry(chemistry)
    y = np.asarray(mean_temperature_degc, dtype=float) / 10.0
    alpha_hour = _cubic(_ALPHA_POLY[chem][_alpha_band_key(dod)], y)
    return np.clip(alpha_hour * HOURS_PER_YEAR, 0.0, None)


def _load_band_coefficients() -> dict[str, Any]:
    global _BAND_COEFF_CACHE
    if _BAND_COEFF_CACHE is None:
        if not _BAND_COEFF_PATH.exists():
            raise InputValidationError(f"Missing depth-band coefficient file: {_BAND_COEFF_PATH}.")
        _BAND_COEFF_CACHE = json.loads(_BAND_COEFF_PATH.read_text(encoding="utf-8"))
    return _BAND_COEFF_CACHE


def _psi_at(edges: np.ndarray, psi_edges: np.ndarray, d: float) -> np.ndarray:
    i1 = int(np.clip(np.searchsorted(edges, d, side="right"), 1, len(edges) - 1))
    i0 = i1 - 1
    frac = (d - edges[i0]) / (edges[i1] - edges[i0])
    return psi_edges[i0] + frac * (psi_edges[i1] - psi_edges[i0])


def evaluate_band_marginals(
    *,
    chemistry: str,
    depth_of_discharge: float,
    temperature_degc: np.ndarray,
    n_bands: int,
    user_cycle_life: float | None = None,
) -> dict[str, Any]:
    """Per-SOC-band marginal cycle-fade costs c_k(T), shape (n_bands, *temperature.shape).

    Band 0 is the shallowest (top-of-charge) slice, band n_bands-1 the deepest. Units:
    fraction of nameplate capacity lost per unit of depth-fraction traversed, i.e. times
    kWh of discharge through the band gives kWh of capacity lost.
    """
    chem = normalize_chemistry(chemistry)
    n_bands = int(n_bands)
    if not (1 <= n_bands <= MAX_BANDS):
        raise InputValidationError(f"n_soc_bands must be within [1, {MAX_BANDS}] (got {n_bands}).")
    dod = float(depth_of_discharge)
    if not (0.0 < dod <= 1.0):
        raise InputValidationError("battery depth_of_discharge must be within (0, 1].")
    temp = np.asarray(temperature_degc, dtype=float)
    if temp.size and not np.isfinite(temp).all():
        raise InputValidationError("ambient temperature series contains non-finite values.")

    entry = _load_band_coefficients()["ck_bands"][chem]
    edges = np.asarray(entry["edges"], dtype=float)
    y = temp / 10.0
    c_fine = np.clip(np.stack([_cubic(tuple(p), y) for p in entry["poly"]], axis=0), 0.0, None)
    dwidth = np.diff(edges).reshape((-1,) + (1,) * temp.ndim)
    psi_edges = np.concatenate([np.zeros((1,) + temp.shape), np.cumsum(c_fine * dwidth, axis=0)], axis=0)
    use_edges = np.linspace(0.0, dod, n_bands + 1)
    psi_use = np.stack([_psi_at(edges, psi_edges, float(d)) for d in use_edges], axis=0)
    width = np.diff(use_edges).reshape((-1,) + (1,) * temp.ndim)
    c_k = np.clip(np.diff(psi_use, axis=0) / width, 0.0, None) * cycle_life_scaling(chem, user_cycle_life)
    return {
        "c_k": c_k,
        "usable_band_edges": use_edges,
        "chemistry": chem,
        "cycle_life_scaling": cycle_life_scaling(chem, user_cycle_life),
    }
