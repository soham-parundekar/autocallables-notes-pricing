"""Greeks by bump-and-revalue on common random numbers.

Every revaluation re-seeds the generator identically, so the base case and each
bumped case draw the same normals. Without that, a 1% spot bump on a note with a
standard error near $1 per $1,000 would be measuring simulation noise: the bump
moves the value by a few dollars, and two independent runs disagree by a couple
of dollars on their own. With common random numbers the noise cancels almost
entirely and what is left is the sensitivity.

Three sensitivities, each defined to match how the note is actually hedged.

Delta is per underlying and central: a worst-of note's exposure to one index
depends on where the other two are, so there is no single delta and the three
are reported separately along with their sum.

Vega is bucketed by maturity rather than quoted as one number. A desk hedging
this note is short vol in specific expiries, and the bucket profile shows where:
a note that is likely to be called inside a year has almost no sensitivity to
five-year vol even when it has five years to run.

Correlation is bumped as a uniform shift of every off-diagonal entry, then
repaired to stay a valid correlation matrix. The repair matters: shifting NDX
and SPX up from 0.928 saturates, so the shift that lands is smaller than the one
requested, and the Greek is reported per unit of *achieved* shift.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..pricer.engine import build_controls, build_inputs, run_simulation
from ..pricer.payoff import price


def _value(note_record, cfg, surfaces, root, *, n_paths, seed, spread=0.0, **scenario) -> dict:
    inputs = build_inputs(note_record, cfg, surfaces, root, **scenario)
    sim = run_simulation(inputs, cfg, n_paths=n_paths, seed=seed)
    controls = build_controls(inputs, sim) if cfg["monte_carlo"].get("control_variate") else None
    out = price(inputs.terms, sim, inputs.curve, funding_spread=spread, controls=controls)
    out["_achieved_corr_shift"] = inputs.correlation_diagnostics.get(
        "correlation_shift_achieved", 0.0
    )
    return out


def spot_deltas(
    note_record: dict, cfg: dict, surfaces: dict, root: Path,
    *, n_paths: int, seed: int, bump: float = 0.01, spread: float = 0.0,
) -> list[dict]:
    """Central delta per underlying, in value change per 1% move.

    Only the bumped asset's starting level moves; its barriers stay where the
    term sheet put them. That separation is the whole point, and getting it
    wrong is easy: every barrier here is a fraction of the initial level, so
    scaling the initial level scales the barriers with it and performance is
    identical at every observation date. Measured that way the "delta" is
    nothing but a shift in where the local-vol surface is sampled, and it came
    out largest on the underlying with the steepest skew rather than the one
    most likely to be the worst performer.

    This is a local-volatility delta, which is a specific convention worth
    naming. Local vol is a fixed function of level and time, so moving spot
    moves the path onto a different part of the same surface and the asset's
    effective volatility changes with it. That skew term is part of the answer
    rather than contamination, and it is why the S&P leg still carries more
    delta than the Nasdaq leg even though the Nasdaq is likelier to be the
    weakest performer. A sticky-moneyness delta, which would hold the forward
    ratio fixed, is a different and equally legitimate number.
    """
    tickers = [u["ticker"] for u in note_record["underlyings"]]
    out = []
    for i, ticker in enumerate(tickers):
        up = np.ones(len(tickers)); up[i] = 1.0 + bump
        down = np.ones(len(tickers)); down[i] = 1.0 - bump
        v_up = _value(note_record, cfg, surfaces, root, n_paths=n_paths, seed=seed,
                      spread=spread, spot_scale=up)["value"]
        v_down = _value(note_record, cfg, surfaces, root, n_paths=n_paths, seed=seed,
                        spread=spread, spot_scale=down)["value"]
        out.append({
            "underlying": ticker,
            "delta_per_1pct": (v_up - v_down) / 2.0,
            "value_up": v_up,
            "value_down": v_down,
            "gamma_per_1pct_squared": v_up + v_down,  # completed by the caller with the base
        })
    return out


def bucketed_vega(
    note_record: dict, cfg: dict, surfaces: dict, root: Path,
    *, n_paths: int, seed: int, buckets: list[float], bump: float = 0.01,
    spread: float = 0.0, base_value: float | None = None,
) -> list[dict]:
    """Vega by maturity bucket, per 1 vol point, summed across underlyings.

    The shift is applied to every underlying at once inside each bucket, which
    is the sensitivity to a parallel move in that part of the term structure.
    Shifting one underlying at a time would be a finer decomposition than the
    data supports, since the three surfaces are calibrated from the same session.
    """
    edges = [0.0, *buckets]
    base = base_value
    if base is None:
        base = _value(note_record, cfg, surfaces, root,
                      n_paths=n_paths, seed=seed, spread=spread)["value"]
    out = []
    for low, high in zip(edges, edges[1:]):
        bumped = _value(note_record, cfg, surfaces, root, n_paths=n_paths, seed=seed,
                        spread=spread, bucket_shift=(low, high, bump))["value"]
        out.append({
            "bucket_years": f"{low:g}-{high:g}",
            "vega_per_vol_point": (bumped - base) / (bump * 100.0),
            "value_bumped": bumped,
        })
    return out


def correlation_sensitivity(
    note_record: dict, cfg: dict, surfaces: dict, root: Path,
    *, n_paths: int, seed: int, shift: float = 0.05, spread: float = 0.0,
) -> dict:
    """Value change for a uniform correlation shift, both directions.

    The sign is the point of this one. In a worst-of structure the investor is
    short a down-and-in put on the weakest performer, and lower correlation
    raises the chance that at least one underlying falls through its barrier,
    which makes that put more valuable and the note worth less. So the investor
    is long correlation, and the numbers here confirm it rather than asserting
    it.
    """
    up = _value(note_record, cfg, surfaces, root, n_paths=n_paths, seed=seed,
                spread=spread, correlation_shift=shift)
    down = _value(note_record, cfg, surfaces, root, n_paths=n_paths, seed=seed,
                  spread=spread, correlation_shift=-shift)
    achieved = 0.5 * (abs(up["_achieved_corr_shift"]) + abs(down["_achieved_corr_shift"]))
    gap = up["value"] - down["value"]
    return {
        "requested_shift": shift,
        "achieved_shift": achieved,
        "value_up": up["value"],
        "value_down": down["value"],
        "d_value_per_0.05": gap / 2.0 * (0.05 / achieved) if achieved > 1e-9 else float("nan"),
        "investor_is_long_correlation": bool(gap > 0),
        "prob_loss_up": up["prob_principal_loss"],
        "prob_loss_down": down["prob_principal_loss"],
    }


def economic_decomposition(
    note_record: dict, cfg: dict, surfaces: dict, root: Path,
    *, n_paths: int, seed: int, spread: float = 0.0,
) -> dict:
    """Split the note into the pieces a desk would actually book.

    The brief's four components, measured rather than asserted:

      funding bond   present value of principal at the discount curve in use
      coupon strip   present value of the contingent coupons
      worst-of put   what the principal-at-risk feature costs, as the gap
                     between the funding bond and the expected redemption
      issuer call    the difference between the note valued without the call
                     and with it

    The pieces do not sum to the note value exactly, and are not meant to: the
    call interacts with the coupon strip, since calling stops future coupons as
    well as capping the redemption. The residual is reported so that
    interaction is visible instead of being quietly absorbed.
    """
    from ..pricer.payoff import coupon_matrix, terminal_payoff

    inputs = build_inputs(note_record, cfg, surfaces, root)
    sim = run_simulation(inputs, cfg, n_paths=n_paths, seed=seed)
    terms = inputs.terms

    discount = np.array([
        float(inputs.curve.discount(t)[0]) * np.exp(-spread * t)
        for t in terms.payment_times
    ])
    worst = sim.worst.astype(float)
    coupons = coupon_matrix(terms, worst)
    terminal = terminal_payoff(terms, sim)

    with_call = price(terms, sim, inputs.curve, funding_spread=spread)["value"]

    no_call_inputs = build_inputs(note_record, cfg, surfaces, root, disable_call=True)
    no_call_sim = run_simulation(no_call_inputs, cfg, n_paths=n_paths, seed=seed)
    no_call = price(no_call_inputs.terms, no_call_sim, no_call_inputs.curve,
                    funding_spread=spread)["value"]

    funding_bond = terms.denomination * discount[-1]
    coupon_strip = float((coupons * discount[None, :]).sum(axis=1).mean())
    expected_redemption = float(terminal.mean() * discount[-1])

    return {
        "funding_bond": funding_bond,
        "coupon_strip": coupon_strip,
        "worst_of_put_cost": funding_bond - expected_redemption,
        "issuer_call_cost": no_call - with_call,
        "value_without_call": no_call,
        "value_with_call": with_call,
        "sum_of_parts_without_call": funding_bond + coupon_strip - (funding_bond - expected_redemption),
        "residual_vs_no_call": no_call - (coupon_strip + expected_redemption),
    }
