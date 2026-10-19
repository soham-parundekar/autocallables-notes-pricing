"""Wiring: term sheet plus surfaces plus correlation, valued as of the pricing date.

Two conventions set here matter more than anything else in this file, because
they are how the project handles the two-day gap between the notes' pricing date
and the only available option snapshot.

Valuation happens as of the note's own pricing date, not the snapshot date.
Every observation and payment time is measured from the pricing date, so the
note's full life is modelled, and the Treasury discount curve is taken as of the
pricing date too, which is available daily. The only input that cannot be dated
correctly is the volatility surface.

The surface is transferred by moneyness, not by level. Each underlying's
forward curve is rescaled so that it starts from the initial level disclosed in
the filing rather than from the snapshot close. Since the surface is
parameterised in log-moneyness k = ln(K/F), rescaling makes k = 0 mean at the
money for the note, and the fitted smile shape and term structure carry across
unchanged. That is the standard sticky-moneyness assumption, and it is the
reason the spot moves between the 7th and the 9th - up to 3.5% on SMH, under 1%
on the indices - do not contaminate the strike mapping. What remains is the
genuine two-day change in the shape and level of the surface itself, which
src/analysis/timing_risk.py measures against the Cboe vol indices instead of
leaving it as an unquantified caveat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np

from ..data.rates import ZeroCurve, curve_for
from ..vol.calibrate import load_surface
from ..vol.local_vol import ForwardCurve, LocalVolSurface
from .correlation import bump_correlation, cholesky, correlation_matrix
from .payoff import NoteTerms, build_terms, price
from .simulate import LocalVolGrid, simulate


# (t_low, t_high, delta): add delta to the implied vol of every slice whose
# maturity falls in [t_low, t_high).
BucketShift = tuple[float, float, float]


@dataclass
class PricingInputs:
    """Everything one valuation needs, assembled and reusable across scenarios."""

    terms: NoteTerms
    grids: list[LocalVolGrid]
    cholesky_factor: np.ndarray
    curve: ZeroCurve
    correlation: np.ndarray
    correlation_diagnostics: dict
    surfaces: list[LocalVolSurface]
    # The initial levels as the term sheet fixes them. Barriers are fractions of
    # these, so they must not move when a spot bump does.
    reference_levels: np.ndarray | None = None


def rescale_forward_curve(curve: ForwardCurve, initial_level: float) -> ForwardCurve:
    """Shift a forward curve onto the note's disclosed initial level.

    Multiplying every forward by initial/spot preserves the carry exactly and
    moves the whole curve so that k = 0 is at the money for this note. Without
    it, a note struck on the 7th would be looked up against the 9th's spot and
    every barrier would sit at the wrong point on the smile.
    """
    factor = initial_level / curve.spot
    return ForwardCurve(
        times=curve.times.copy(),
        forwards=curve.forwards * factor,
        spot=initial_level,
    )


def build_inputs(
    note_record: dict,
    cfg: dict,
    surface_records: dict,
    repo_root: Path,
    *,
    extrapolation: str | None = None,
    tail_vol_shift: float = 0.0,
    vol_scale: float = 1.0,
    bucket_shift: BucketShift | dict[str, BucketShift] | None = None,
    correlation_shift: float = 0.0,
    flat_vol: bool = False,
    spot_scale: np.ndarray | None = None,
    disable_call: bool = False,
) -> PricingInputs:
    """Assemble pricing inputs, optionally perturbed for a risk scenario.

    `vol_scale` multiplies every local vol (used for vega), `flat_vol` replaces
    the surface with a single at-the-money Black vol per underlying (used for
    the model-risk comparison), and `spot_scale` moves the starting levels (used
    for delta).

    `bucket_shift` is (t_low, t_high, delta) and moves implied vol by delta on
    every slice in that maturity band, which is how both the bucketed vega and
    the timing adjustment are produced. Passing a mapping keyed by ticker shifts
    one leg and leaves the rest alone, which is what prices the ETF-proxy bias:
    DIA's implied vols sit about half a point above DJX's (measured in
    src/vol/proxy_check.py), and correcting the Dow leg of N2 must not touch its
    Nasdaq and Russell legs.

    `tail_vol_shift` is a different thing despite the similar name: it belongs
    to the frozen_shifted extrapolation mode and only bites past the last listed
    expiry. It is not a way to shift the surface.
    """
    pricing_date = _as_date(note_record["pricing_date"])
    terms = build_terms(note_record, valuation_date=pricing_date)
    curve = curve_for(pricing_date, repo_root / cfg["snapshot"]["treasury_csv"])

    k_bounds = tuple(cfg["surface"]["k_clip"])
    extrap = extrapolation or cfg["surface"]["extrapolation"]

    surfaces, grids = [], []
    horizon = float(terms.observation_times[-1]) * 1.02
    for ticker, initial in zip(terms.tickers, terms.initial_levels):
        leg_bucket = (bucket_shift.get(ticker) if isinstance(bucket_shift, dict)
                      else bucket_shift)
        surface = load_surface(
            surface_records[ticker],
            extrapolation=extrap,
            tail_vol_shift=tail_vol_shift,
            k_bounds=k_bounds,
            bucket_shift=leg_bucket,
        )
        surface.forward_curve = rescale_forward_curve(surface.forward_curve, float(initial))
        surfaces.append(surface)
        grid = LocalVolGrid(surface, t_max=horizon)
        if flat_vol:
            # One at-the-money vol for the whole surface, read at the note's own
            # maturity. This is the comparison the brief asks for: how much of
            # the note's value depends on the smile rather than on the level.
            atm = float(surface.implied_vol(np.array([0.0]), float(terms.observation_times[-1]))[0])
            grid.table[:] = atm
        if vol_scale != 1.0:
            grid.table = np.clip(grid.table * vol_scale, 0.01, 2.0)
        grids.append(grid)

    correlation, diagnostics = correlation_matrix(
        terms.tickers, repo_root / "data/raw",
        as_of=pricing_date, lookback=cfg["correlation"]["lookback_days"],
    )
    repaired, achieved_shift = False, 0.0
    if correlation_shift:
        correlation, repaired, achieved_shift = bump_correlation(correlation, correlation_shift)
    diagnostics["bump_required_repair"] = repaired
    diagnostics["correlation_shift_requested"] = correlation_shift
    diagnostics["correlation_shift_achieved"] = achieved_shift

    if disable_call:
        # Diagnostic only: the note is not what the term sheet describes once
        # the issuer's call is removed. It isolates how much of the gap to the
        # disclosed value the call accounts for.
        terms.call_eligible = np.zeros_like(terms.call_eligible)

    reference_levels = terms.initial_levels.copy()
    if spot_scale is not None:
        terms.initial_levels = terms.initial_levels * spot_scale

    return PricingInputs(
        terms=terms, grids=grids, cholesky_factor=cholesky(correlation),
        curve=curve, correlation=correlation,
        correlation_diagnostics=diagnostics, surfaces=surfaces,
        reference_levels=reference_levels,
    )


def run_simulation(inputs: PricingInputs, cfg: dict, *, n_paths: int | None = None,
                   seed: int | None = None):
    """Simulate paths for one note.

    The starting levels handed to the simulation are the note's own initial
    levels, and the drift at each step comes from that underlying's rescaled
    forward curve, so each asset carries its own term structure of carry rather
    than one blended number.
    """
    mc = cfg["monte_carlo"]
    terms = inputs.terms

    def carry_curve(asset_index: int, t: float) -> float:
        return inputs.surfaces[asset_index].forward_curve.carry(t)

    knock_in_level = (
        terms.downside_trigger if terms.downside_observation == "american" else None
    )
    return simulate(
        inputs.grids,
        terms.initial_levels,
        reference_levels=inputs.reference_levels,
        carry=np.array([carry_curve(i, 0.0) for i in range(len(inputs.grids))]),
        cholesky=inputs.cholesky_factor,
        observation_times=terms.observation_times,
        n_paths=n_paths or mc["paths"],
        steps_per_year=mc["steps_per_year"],
        seed=seed if seed is not None else mc["seed"],
        antithetic=mc["antithetic"],
        knock_in_level=knock_in_level,
        knock_in_window=terms.knock_in_window,
        carry_curve=carry_curve,
    )


def build_controls(inputs: PricingInputs, sim) -> tuple[np.ndarray, np.ndarray]:
    """European puts at each underlying's downside trigger, as control variates.

    The strike is the note's own trigger level, so the control is the single
    vanilla most correlated with what drives the note's loss. Its exact value
    comes from the calibrated smile at that strike and maturity, through the
    same Black inversion used to build the surface, and the repricing check in
    src/vol/reprice_check.py is what licenses treating it as exact.

    Payoffs are expressed per unit of initial level and scaled by the note's
    denomination so they are on the same scale as the note's value.
    """
    import py_lets_be_rational as lbr

    terms = inputs.terms
    horizon = float(terms.observation_times[-1])
    discount = float(inputs.curve.discount(horizon)[0])
    strike_ratio = terms.downside_trigger

    reference = (
        terms.initial_levels if inputs.reference_levels is None else inputs.reference_levels
    )

    payoffs, expected = [], []
    for index, surface in enumerate(inputs.surfaces):
        # The strike is a fraction of the reference level the term sheet fixed,
        # while the forward grows from wherever the paths start. Those differ
        # whenever a spot bump is applied, and conflating them would price the
        # control off the unbumped forward: the control's value would then be
        # wrong for the bumped leg and would bias the delta it is supposed to
        # help measure.
        start = float(terms.initial_levels[index])
        base = float(reference[index])
        forward = float(surface.forward_curve(horizon)[0]) * (start / surface.forward_curve.spot)

        strike = strike_ratio * base
        forward_ratio = forward / base
        vol = float(surface.implied_vol(np.array([np.log(strike / forward)]), horizon)[0])

        # Undiscounted Black put on the normalised asset, then discounted.
        true_price = discount * float(lbr.black(forward_ratio, strike_ratio, vol, horizon, -1))
        pathwise = discount * np.maximum(strike_ratio - sim.per_asset_final[:, index], 0.0)

        payoffs.append(terms.denomination * pathwise)
        expected.append(terms.denomination * true_price)

    return np.column_stack(payoffs), np.array(expected)


def value(
    note_record: dict,
    cfg: dict,
    surface_records: dict,
    repo_root: Path,
    *,
    funding_spread: float = 0.0,
    n_paths: int | None = None,
    seed: int | None = None,
    **scenario,
) -> dict:
    """Build inputs, simulate and price in one call."""
    inputs = build_inputs(note_record, cfg, surface_records, repo_root, **scenario)
    sim = run_simulation(inputs, cfg, n_paths=n_paths, seed=seed)
    controls = build_controls(inputs, sim) if cfg["monte_carlo"].get("control_variate") else None
    out = price(inputs.terms, sim, inputs.curve, funding_spread=funding_spread, controls=controls)
    out.update(
        issuer=inputs.terms.issuer,
        cusip=inputs.terms.cusip,
        tickers=inputs.terms.tickers,
        disclosed_estimated_value=inputs.terms.disclosed_estimated_value,
        selling_commission=inputs.terms.selling_commission,
        call_style=inputs.terms.call_style,
        pricing_date=inputs.terms.pricing_date.isoformat(),
        maturity_date=inputs.terms.maturity_date.isoformat(),
        tenor_years=float(inputs.terms.observation_times[-1]),
        coupon_rate=inputs.terms.coupon_rate,
        coupon_barrier=inputs.terms.coupon_barrier,
        downside_trigger=inputs.terms.downside_trigger,
        downside_observation=inputs.terms.downside_observation,
        correlation_offdiag=inputs.correlation[np.triu_indices(len(inputs.terms.tickers), 1)].tolist(),
        correlation_shift_achieved=inputs.correlation_diagnostics.get("correlation_shift_achieved", 0.0),
        correlation_repaired=inputs.correlation_diagnostics.get("bump_required_repair", False),
        realised_vol=inputs.correlation_diagnostics["annualised_vol"],
        simulation=sim.diagnostics,
    )
    return out


def _as_date(value_in) -> date:
    if isinstance(value_in, date):
        return value_in
    return datetime.fromisoformat(str(value_in)).date()
