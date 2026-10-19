"""Back out the funding spread that reconciles the model with the disclosed value.

The disclosure in every one of these supplements says the estimated value is
derived from the issuer's own models together with its internal funding rate,
and that this rate is higher than the issuer's ordinary unsecured borrowing
rate. Discounting at a higher rate lowers the value, so the arithmetic the brief
sets out is: find the spread s over Treasuries at which the model reproduces the
issuer's number,

    V(s) = E^Q [ sum_i exp(-(r + s) t_i) CF_i ] = EV_disclosed

and read s* as what the issuer's funding assumption is worth. V is monotone
decreasing in s, so Brent on a bracket is the right tool.

What this module reports, and why it reports more than one number
----------------------------------------------------------------
Solved at face value against the base model, every note in this sample returns a
*negative* s*, which cannot be a funding spread: it would mean the issuer
discounted its own liability more lightly than Treasuries. So the inversion is
run under two call assumptions rather than one, because the issuer's call is the
largest discretionary input in the model and the only one that moves the value
by enough to matter:

  optimal      The issuer exercises its Bermudan call optimally, by
               Longstaff-Schwartz. This is the correct valuation of a note whose
               call belongs to the issuer, and for the Morgan Stanley notes it
               is the disclosed contract term.

  no_early_call  The call is ignored and the note runs to maturity. This is not
               a valuation of the note as written; it is a diagnostic, included
               because it isolates how much of the gap the call accounts for.

Reporting s* under both, next to the issuer's traded bond spread, says something
a single number cannot: whether the disclosed value is consistent with the
issuer charging for the option it holds.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.optimize import brentq

from ..pricer.engine import build_inputs, run_simulation
from ..pricer.payoff import price


def value_at_spread(inputs, sim, spread: float, controls=None) -> float:
    """Model value per $1,000 at one funding spread."""
    return price(inputs.terms, sim, inputs.curve,
                 funding_spread=spread, controls=controls)["value"]


def implied_spread(
    inputs,
    sim,
    target: float,
    *,
    bracket: tuple[float, float] = (-0.03, 0.15),
    tolerance: float = 1e-8,
    controls=None,
) -> dict:
    """Solve V(s) = target for s, reporting honestly when no root exists.

    A root outside the bracket is not an error to be silently clamped. If the
    model cannot reach the disclosed value at any plausible spread, that is the
    result, and the endpoints are reported so the size of the shortfall is
    visible.
    """
    lo, hi = bracket
    v_lo = value_at_spread(inputs, sim, lo, controls)
    v_hi = value_at_spread(inputs, sim, hi, controls)

    if not (min(v_lo, v_hi) <= target <= max(v_lo, v_hi)):
        return {
            "spread": float("nan"),
            "bracketed": False,
            "value_at_low_spread": v_lo,
            "value_at_high_spread": v_hi,
            "target": target,
            "shortfall": target - v_lo,
        }

    root = brentq(
        lambda s: value_at_spread(inputs, sim, s, controls) - target,
        lo, hi, xtol=tolerance, maxiter=200,
    )
    return {
        "spread": float(root),
        "bracketed": True,
        "value_at_low_spread": v_lo,
        "value_at_high_spread": v_hi,
        "target": target,
        "shortfall": 0.0,
    }


def fair_coupon(
    note_record: dict,
    cfg: dict,
    surface_records: dict,
    repo_root: Path,
    *,
    credit_spread: float,
    n_paths: int,
    seed: int,
    bracket: tuple[float, float] = (0.0, 0.60),
) -> dict:
    """Coupon rate that makes the note worth issue price minus commission.

    The brief's second lens on the same margin. The note is discounted at the
    risk-free rate plus the issuer's *market* credit spread, not its internal
    funding rate, and the coupon is solved so the note is worth what the
    investor actually pays net of the disclosed commission. The gap between that
    coupon and the offered one is the margin expressed in coupon terms, which is
    easier to read than a price difference.

    Re-simulating per trial coupon would be wasteful and wrong: the coupon does
    not affect the paths, only the cashflows, so the simulation is reused and
    only the coupon amount changes between iterations.
    """
    import copy

    target = note_record["denomination"] - note_record["selling_commission"]
    periods = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}[
        note_record["coupon"]["frequency"]
    ]

    base_inputs = build_inputs(note_record, cfg, surface_records, repo_root)
    sim = run_simulation(base_inputs, cfg, n_paths=n_paths, seed=seed)

    def value_for(rate: float) -> float:
        trial = copy.copy(base_inputs.terms)
        trial.coupon_rate = rate
        trial.coupon_amount = note_record["denomination"] * rate / periods
        return price(trial, sim, base_inputs.curve, funding_spread=credit_spread)["value"]

    lo, hi = bracket
    v_lo, v_hi = value_for(lo), value_for(hi)
    if not (min(v_lo, v_hi) <= target <= max(v_lo, v_hi)):
        return {
            "fair_coupon": float("nan"), "bracketed": False,
            "offered_coupon": note_record["coupon"]["annual_rate"],
            "value_at_zero_coupon": v_lo, "value_at_max_coupon": v_hi, "target": target,
        }

    root = brentq(lambda r: value_for(r) - target, lo, hi, xtol=1e-10, maxiter=200)
    return {
        "fair_coupon": float(root),
        "bracketed": True,
        "offered_coupon": float(note_record["coupon"]["annual_rate"]),
        "coupon_gap": float(note_record["coupon"]["annual_rate"] - root),
        "credit_spread_used": credit_spread,
        "target": target,
        "value_at_zero_coupon": v_lo,
        "value_at_max_coupon": v_hi,
    }


def margin_decomposition(note_record: dict) -> dict:
    """Issue price minus disclosed value minus commission, straight from the filing.

    No model enters this. It is the issuer's own arithmetic, and it is the one
    margin number in the project that carries no modelling assumption at all.
    """
    denomination = float(note_record["denomination"])
    disclosed = float(note_record["disclosed_estimated_value"])
    commission = float(note_record["selling_commission"])
    return {
        "note_id": note_record["id"],
        "issuer": note_record["issuer"],
        "denomination": denomination,
        "disclosed_estimated_value": disclosed,
        "selling_commission": commission,
        "ev_gap": denomination - disclosed,
        "residual_margin": denomination - disclosed - commission,
        "ev_gap_pct": 100.0 * (denomination - disclosed) / denomination,
        "residual_margin_pct": 100.0 * (denomination - disclosed - commission) / denomination,
    }
