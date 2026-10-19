"""How much of each note's value depends on modelling choices rather than data.

Three separate questions, each answered by repricing the same note under a
different assumption and reporting the difference. All three run on common
random numbers, so the differences are not simulation noise.

Smile risk. Reprice with one flat Black vol per underlying, read at the money
at the note's own maturity, against the full local-vol surface. This is the
brief's model-risk comparison, and it answers how much the note's economics
depend on getting the skew right rather than just the level. For a structure
whose whole downside sits 25 to 40% below spot, the answer should be large, and
a small answer would mean something was wrong with the surface.

Extrapolation risk. The five-year notes reach well past the longest listed
expiry on two of their three underlyings: NDX quotes stop at 3.2 years and the
Russell at 2.2, against notes maturing in 2031. The base case freezes the
implied smile and lets total variance grow linearly in maturity. The alternative
carries the last observed variance slope forward instead, which keeps the
upward term structure going rather than flattening it. The spread between them
is what the extrapolation is worth, and it belongs next to the long-dated
results rather than in a footnote.

Timing risk. The notes priced on 7 October and the only free option snapshot is
the 9th. That gap is measurable rather than unknown: Cboe publishes VIX, VXN and
RVX daily, so the two-day change in 30-day implied vol is observable per
underlying. Repricing with the surface shifted by exactly that move gives a
quantified band on every model value instead of an unquantified caveat.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

import numpy as np

from ..pricer.engine import build_inputs, run_simulation
from ..pricer.payoff import price

# Which Cboe volatility index tracks which underlying. The Dow and SMH have no
# published index of their own, so their timing move is taken from VIX and VXN
# respectively, which is stated rather than hidden.
VOL_INDEX = {
    "SPX": ("cboe_vix_history.csv", "VIX", "exact"),
    "NDX": ("cboe_vxn_history.csv", "VXN", "exact"),
    "RUT": ("cboe_rvx_history.csv", "RVX", "exact"),
    "DJX": ("cboe_vix_history.csv", "VIX", "proxy: no published Dow vol index"),
    "SMH": ("cboe_vxn_history.csv", "VXN", "proxy: no published semiconductor vol index"),
}


def vol_index_move(ticker: str, raw_dir: Path, start: date, end: date) -> dict:
    """Change in 30-day implied vol between two dates, in vol points."""
    filename, label, basis = VOL_INDEX[ticker]
    levels: dict[date, float] = {}
    with open(raw_dir / filename) as handle:
        for row in csv.reader(handle):
            if not row or len(row) < 2:
                continue
            try:
                stamp = datetime.strptime(row[0], "%m/%d/%Y").date()
                levels[stamp] = float(row[-1])
            except (ValueError, IndexError):
                continue
    if start not in levels or end not in levels:
        return {"ticker": ticker, "index": label, "basis": basis, "move_vol_points": float("nan")}
    return {
        "ticker": ticker,
        "index": label,
        "basis": basis,
        "level_start": levels[start],
        "level_end": levels[end],
        # Divided by 100 because the indices are quoted in percent.
        "move_vol_points": (levels[end] - levels[start]) / 100.0,
    }


def flat_vol_comparison(
    note_record: dict, cfg: dict, surfaces: dict, root: Path,
    *, n_paths: int, seed: int, base_value: float,
) -> dict:
    """Local-vol value against a single at-the-money Black vol per underlying."""
    flat = build_inputs(note_record, cfg, surfaces, root, flat_vol=True)
    sim = run_simulation(flat, cfg, n_paths=n_paths, seed=seed)
    out = price(flat.terms, sim, flat.curve)
    atm = [
        float(s.implied_vol(np.array([0.0]), float(flat.terms.observation_times[-1]))[0])
        for s in flat.surfaces
    ]
    return {
        "local_vol_value": base_value,
        "flat_vol_value": out["value"],
        "difference": out["value"] - base_value,
        "difference_pct": 100.0 * (out["value"] - base_value) / base_value,
        "flat_vols_used": dict(zip(flat.terms.tickers, [round(v, 4) for v in atm])),
        "flat_vol_prob_loss": out["prob_principal_loss"],
        "flat_vol_prob_called": out["prob_called"],
    }


def extrapolation_comparison(
    note_record: dict, cfg: dict, surfaces: dict, root: Path,
    *, n_paths: int, seed: int, base_value: float,
) -> dict:
    """Base frozen-smile extrapolation against carrying the variance slope forward."""
    alt = build_inputs(note_record, cfg, surfaces, root, extrapolation="variance_slope")
    sim = run_simulation(alt, cfg, n_paths=n_paths, seed=seed)
    out = price(alt.terms, sim, alt.curve)

    horizon = float(alt.terms.observation_times[-1])
    reliance = {}
    for ticker, surface in zip(alt.terms.tickers, alt.surfaces):
        last = surface.last_t
        reliance[ticker] = {
            "longest_listed_expiry_years": round(last, 3),
            "note_horizon_years": round(horizon, 3),
            "extrapolated_years": round(max(horizon - last, 0.0), 3),
            "fraction_extrapolated": round(max(horizon - last, 0.0) / horizon, 4),
        }
    return {
        "frozen_smile_value": base_value,
        "variance_slope_value": out["value"],
        "difference": out["value"] - base_value,
        "difference_pct": 100.0 * (out["value"] - base_value) / base_value,
        "extrapolation_reliance": reliance,
    }


def timing_comparison(
    note_record: dict, cfg: dict, surfaces: dict, root: Path,
    *, n_paths: int, seed: int, base_value: float, snapshot_date: date,
) -> dict:
    """Reprice with the surface shifted by the measured move over the date gap.

    One shift is applied to all underlyings, taken as the average of their own
    measured moves, because the surface shift in build_inputs is a single
    parallel number. The per-underlying moves are reported alongside so the
    spread between them is visible: over 7 to 9 October 2026 the Russell's
    30-day vol fell four times as far as the S&P's.
    """
    pricing_date = note_record["pricing_date"]
    if isinstance(pricing_date, str):
        pricing_date = datetime.fromisoformat(pricing_date).date()

    moves = [
        vol_index_move(u["ticker"], root / "data/raw", pricing_date, snapshot_date)
        for u in note_record["underlyings"]
    ]
    valid = [m["move_vol_points"] for m in moves if np.isfinite(m["move_vol_points"])]
    if not valid:
        return {"moves": moves, "applied_shift": float("nan"), "difference": float("nan")}

    # The snapshot is later than the pricing date, so to approximate the
    # pricing-date surface the measured move is removed, not added.
    #
    # The shift goes through bucket_shift over an unbounded maturity range, so
    # every slice moves. The frozen_shifted extrapolation mode would only have
    # shifted the part of the surface beyond the longest listed expiry, which is
    # not what a change in the whole surface means.
    shift = -float(np.mean(valid))
    adjusted = build_inputs(
        note_record, cfg, surfaces, root,
        bucket_shift=(0.0, 1e9, shift),
    )
    sim = run_simulation(adjusted, cfg, n_paths=n_paths, seed=seed)
    out = price(adjusted.terms, sim, adjusted.curve)

    return {
        "moves": moves,
        "applied_shift_vol_points": shift,
        "snapshot_value": base_value,
        "timing_adjusted_value": out["value"],
        "difference": out["value"] - base_value,
        "difference_pct": 100.0 * (out["value"] - base_value) / base_value,
    }
