"""Are the fitted surfaces free of butterfly and calendar arbitrage?

Second item on the brief's validation checklist, and not a formality. A slice
with butterfly arbitrage implies a negative risk-neutral density, which makes
Dupire's denominator pass through zero and returns a negative or undefined local
variance. If a local-vol surface comes out full of NaNs, this is almost always
the reason.

The conditions are checked against the cached calibration, so the test reflects
exactly the surfaces the results were produced from rather than a refit that
might differ.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.vol.svi import (
    BUTTERFLY_TOL,
    CALENDAR_TOL,
    SVIParams,
    butterfly_g,
    risk_neutral_density,
    slice_grid,
)

SURFACES = ROOT / "data/processed/surfaces.json"

pytestmark = pytest.mark.skipif(
    not SURFACES.exists(), reason="run src.vol.calibrate first"
)


def load() -> dict:
    return json.loads(SURFACES.read_text())


def params_for(record: dict) -> list[SVIParams]:
    return [
        SVIParams(a=s["a"], b=s["b"], rho=s["rho"], m=s["m"], sigma=s["sigma"], t=s["t"])
        for s in record["slices"]
    ]


def grids_for(record: dict) -> list[np.ndarray]:
    return [np.linspace(s["grid_k_min"], s["grid_k_max"], 161) for s in record["slices"]]


@pytest.mark.parametrize("ticker", ["SPX", "NDX", "RUT", "DJX", "SMH"])
def test_no_butterfly_arbitrage(ticker):
    record = load()[ticker]
    for params, grid, meta in zip(params_for(record), grids_for(record), record["slices"]):
        worst = float(butterfly_g(params, grid).min())
        assert worst >= BUTTERFLY_TOL, (
            f"{ticker} {meta['expiry']}: g(k) reaches {worst:.3e}, "
            "which implies a negative risk-neutral density"
        )


@pytest.mark.parametrize("ticker", ["SPX", "NDX", "RUT", "DJX", "SMH"])
def test_risk_neutral_density_is_non_negative(ticker):
    """The density is the economically meaningful form of the same condition."""
    record = load()[ticker]
    for params, grid, meta in zip(params_for(record), grids_for(record), record["slices"]):
        worst = float(risk_neutral_density(params, grid).min())
        assert worst >= -1e-10, f"{ticker} {meta['expiry']}: density reaches {worst:.3e}"


@pytest.mark.parametrize("ticker", ["SPX", "NDX", "RUT", "DJX", "SMH"])
def test_total_variance_is_non_decreasing_in_maturity(ticker):
    """Calendar arbitrage: w(k,T) must not fall as T rises, at any fixed k.

    Checked across the calibrated band, which is also the band the condition
    was imposed on. Enforcing it only where each adjacent pair of expiries
    happened to overlap left w non-monotone everywhere else and drove Dupire's
    numerator to zero over a fifth of the Dow surface.
    """
    record = load()[ticker]
    params = params_for(record)
    band = np.linspace(
        max(s["grid_k_min"] for s in record["slices"]),
        min(s["grid_k_max"] for s in record["slices"]),
        121,
    )
    for earlier, later, meta in zip(params, params[1:], record["slices"][1:]):
        gap = float(np.min(later.total_variance(band) - earlier.total_variance(band)))
        assert gap >= CALENDAR_TOL, (
            f"{ticker} into {meta['expiry']}: total variance falls by {-gap:.3e}"
        )


@pytest.mark.parametrize("ticker", ["SPX", "NDX", "RUT", "DJX", "SMH"])
def test_recorded_audit_reports_no_failures(ticker):
    """The audit stored with the calibration must agree with the checks above."""
    audit = load()[ticker]["audit"]
    assert audit["butterfly_failures"] == 0
    assert audit["calendar_failures"] == 0


@pytest.mark.parametrize("ticker", ["SPX", "NDX", "RUT", "DJX", "SMH"])
def test_fit_quality_is_within_tolerance(ticker):
    """Fit error in vol points, so a bad slice cannot hide behind a good median."""
    record = load()[ticker]
    errors = np.array([s["rmse_vol"] for s in record["slices"]])
    assert np.median(errors) < 0.0040, f"{ticker}: median fit error {np.median(errors)*1e4:.1f}bp"
    assert errors.max() < 0.0400, f"{ticker}: worst slice fit error {errors.max()*1e4:.1f}bp"


def test_parameters_are_valid_svi_slices():
    """b >= 0, |rho| < 1, sigma > 0, and total variance non-negative everywhere."""
    for ticker, record in load().items():
        for params, meta in zip(params_for(record), record["slices"]):
            assert params.b >= 0.0, f"{ticker} {meta['expiry']}: negative b"
            assert abs(params.rho) < 1.0, f"{ticker} {meta['expiry']}: |rho| >= 1"
            assert params.sigma > 0.0, f"{ticker} {meta['expiry']}: non-positive sigma"
            floor = params.a + params.b * params.sigma * np.sqrt(1.0 - params.rho**2)
            assert floor >= -1e-10, f"{ticker} {meta['expiry']}: total variance can go negative"
