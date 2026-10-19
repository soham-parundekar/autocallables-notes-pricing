"""Quote cleaning, forward/discount extraction, and implied vols per expiry.

Three jobs, in the order they have to happen.

First, throw out quotes that are not really quotes. A listed strike with a zero
bid or a two-cent bid against a forty-cent offer is a market maker declining to
trade, and feeding those into a surface fit drags the wings around badly.

Second, back out the forward and the discount factor for each expiry from
put-call parity rather than assuming either. For a European option
C - P = DF * (F - K), so regressing the call-put spread on strike gives DF from
the slope and F from the intercept. That matters more than it looks: it pins the
forward and the implied dividend yield together from traded prices, so no
separate dividend forecast is needed and no borrow assumption is smuggled in.

Third, invert each clean mid price to a Black implied vol. The brief is right
that a hand-rolled Newton solver quietly fails on deep wings, so this uses
Jackel's "Let's Be Rational" through py_lets_be_rational, which recovers vol to
machine precision at forward prices down to 1e-3 and below.

Two wrinkles specific to this project's underlyings:

SPX, NDX, RUT and DJX options are European, so parity holds exactly and all of
the above is clean. SMH is an ETF with American options, where parity holds only
approximately because of the early-exercise premium on in-the-money puts. The
fix is to keep the parity regression near the money, where that premium is
small, and to calibrate the smile from out-of-the-money quotes only. Deep
in-the-money American quotes are dropped rather than modelled.

DJX trades on one hundredth of the Dow, so its strikes are ~516 while the index
the note references is ~51,180. Everything downstream works in log-moneyness
k = ln(K/F), where that factor of 100 cancels, so the DJX surface transfers onto
the index without rescaling. The scale difference is deliberately never undone.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import re

import numpy as np
import pandas as pd
import py_lets_be_rational as lbr

_CONTRACT = re.compile(
    r"^(?P<root>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<kind>[CP])(?P<strike>\d{8})$"
)

# Index options are quoted in nickels at best; ETF options in pennies. Anything
# at or below the tick floor carries no usable vol information.
TICK_FLOOR = {"index": 0.05, "etf": 0.01}

# Day count for option maturities. Calendar/365 is used throughout rather than a
# business-day count so that the option expiries and the notes' observation
# dates sit on one clock.
DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class ExpirySlice:
    """One expiry's forward, discount factor and cleaned implied-vol points."""

    expiry: date
    t: float                  # year fraction to expiry
    forward: float
    discount_factor: float
    parity_rmse: float        # dollars; how well C-P = DF*(F-K) actually held
    parity_n: int             # strikes used in the regression
    strikes: np.ndarray
    log_moneyness: np.ndarray
    implied_vol: np.ndarray
    total_variance: np.ndarray   # w = iv^2 * t, the quantity SVI is fitted in
    weight: np.ndarray           # fit weight, from quote tightness
    spread: np.ndarray           # ask - bid, carried for the repricing test


def load_snapshot(ticker: str, snapshot_dir: Path) -> pd.DataFrame:
    """Read one gzipped Cboe payload into one row per contract."""
    path = snapshot_dir / f"{ticker.upper()}.json.gz"
    with gzip.open(path, "rb") as handle:
        payload = json.load(handle)

    header = payload["data"]
    rows = []
    for contract in header["options"]:
        match = _CONTRACT.match(contract["option"])
        if match is None:
            continue
        rows.append(
            {
                # The root has to be carried, not discarded. Cboe serves both
                # settlement series under one symbol: SPX is AM-settled and
                # SPXW is the PM-settled weekly, and on a third Friday both
                # list the same strikes. Keying slices on the expiry date alone
                # merges two different instruments at every shared strike, which
                # is what wrecked the first SPX fit (62 vol points of error and
                # eighteen calendar violations).
                "root": match["root"],
                "expiry": date(
                    2000 + int(match["yy"]), int(match["mm"]), int(match["dd"])
                ),
                "right": match["kind"],
                "strike": int(match["strike"]) / 1000.0,
                "bid": contract["bid"],
                "ask": contract["ask"],
                "open_interest": contract["open_interest"],
                "volume": contract["volume"],
            }
        )

    chain = pd.DataFrame(rows)
    chain["mid"] = 0.5 * (chain["bid"] + chain["ask"])
    chain["spread"] = chain["ask"] - chain["bid"]
    chain.attrs["underlying_close"] = float(header["close"])
    chain.attrs["ticker"] = ticker.upper()
    return chain


def clean_quotes(
    chain: pd.DataFrame,
    *,
    kind: str = "index",
    max_rel_spread: float = 0.35,
    abs_spread_floor: float = 0.20,
    require_interest: bool = True,
) -> pd.DataFrame:
    """Drop quotes that cannot be traded at anything near their mid.

    The spread test is deliberately two-sided: a quote passes if its spread is
    inside `max_rel_spread` of the mid *or* inside `abs_spread_floor` in
    absolute terms. A pure percentage rule throws away every cheap wing quote
    (a 0.10/0.20 market is a 67% spread but a perfectly real one-tick market),
    and a pure absolute rule keeps far too much of the expensive near-the-money
    strikes.
    """
    floor = TICK_FLOOR[kind]
    keep = (
        (chain["bid"] > 0)
        & (chain["ask"] > chain["bid"])
        & (chain["mid"] > floor)
    )
    tight = chain["spread"] <= np.maximum(abs_spread_floor, max_rel_spread * chain["mid"])
    keep &= tight
    if require_interest:
        # A strike nobody holds and nobody traded today is usually a stale
        # autoquote left over from a previous session.
        keep &= (chain["open_interest"] > 0) | (chain["volume"] > 0)

    out = chain[keep].copy()
    out.attrs.update(chain.attrs)
    return out


def forward_and_discount(
    slice_df: pd.DataFrame,
    *,
    spot: float,
    parity_band: float = 0.12,
    min_strikes: int = 4,
    discount_override: float | None = None,
) -> tuple[float, float, float, int]:
    """Fit C - P = DF * (F - K) across strikes to get (F, DF).

    Only strikes within `parity_band` of spot are used. Near the money both
    sides are liquid and, for American ETF options, the early-exercise premium
    is small enough to ignore; far from the money one leg is a stub quote whose
    noise would tilt the slope and hand back a nonsensical discount factor.

    `discount_override` fixes the discount factor instead of fitting it, and
    should be used for American options. Parity is an identity only for European
    exercise; on an ETF the early-exercise premium on in-the-money puts grows
    with strike, which tilts the regression slope. Left to fit freely on SMH it
    returned a discount factor implying a 1.6% zero rate against 4.3%
    Treasuries. With the discount factor pinned to the curve, only the forward
    is fitted, and it still carries the market's dividend assumption.

    Returns (forward, discount_factor, rmse, n_strikes). The rmse is in dollars
    and is the direct check the brief asks for on this step.
    """
    calls = slice_df[slice_df["right"] == "C"].set_index("strike")["mid"]
    puts = slice_df[slice_df["right"] == "P"].set_index("strike")["mid"]
    both = calls.index.intersection(puts.index)

    lo, hi = spot * (1 - parity_band), spot * (1 + parity_band)
    strikes = np.array([k for k in both if lo <= k <= hi], dtype=float)
    if strikes.size < min_strikes:
        # Fall back to every paired strike rather than give up on the expiry.
        strikes = np.sort(np.array(list(both), dtype=float))
    if strikes.size < 2:
        raise ValueError("not enough paired strikes to identify F and DF")

    spread = np.array([calls[k] - puts[k] for k in strikes], dtype=float)

    if discount_override is not None:
        discount_factor = float(discount_override)
        # F = K + (C - P)/DF at every strike; the mean over near-the-money
        # strikes is the forward, and the scatter is the diagnostic.
        implied = strikes + spread / discount_factor
        forward = float(np.mean(implied))
    else:
        slope, intercept = np.polyfit(strikes, spread, 1)
        discount_factor = -slope
        if not 0.5 < discount_factor <= 1.0005:
            raise ValueError(f"implausible discount factor {discount_factor:.4f}")
        forward = intercept / discount_factor

    fitted = discount_factor * (forward - strikes)
    rmse = float(np.sqrt(np.mean((spread - fitted) ** 2)))
    return float(forward), float(discount_factor), rmse, int(strikes.size)


def _not_outlier(strikes: np.ndarray, ivs: np.ndarray, *, window: int = 9, n_mad: float = 6.0) -> np.ndarray:
    """Flag implied vols that sit far from their neighbours along the strike axis.

    The smile is smooth in strike, so a quote whose vol is several median
    absolute deviations away from the local median is a bad print rather than
    real curvature. A rolling median is used instead of a global one because the
    smile legitimately spans a wide range of vols from wing to wing.
    """
    n = ivs.size
    if n < window + 2:
        return np.ones(n, dtype=bool)
    series = pd.Series(ivs)
    local = series.rolling(window, center=True, min_periods=3).median().to_numpy()
    resid = np.abs(ivs - local)
    mad = np.median(resid[np.isfinite(resid)])
    if not np.isfinite(mad) or mad <= 0:
        return np.ones(n, dtype=bool)
    return resid <= n_mad * mad


def _implied_vol(forward_price: float, f: float, k: float, t: float, right: str) -> float:
    """Jackel's rational-guess inversion on the undiscounted price."""
    q = 1 if right == "C" else -1
    try:
        return float(lbr.implied_volatility_from_a_transformed_rational_guess(forward_price, f, k, t, q))
    except Exception:
        return np.nan


def build_slices(
    chain: pd.DataFrame,
    *,
    valuation_date: date,
    kind: str = "index",
    min_t: float = 14 / DAYS_PER_YEAR,
    max_rel_spread: float = 0.35,
    otm_only: bool = True,
    k_clip: tuple[float, float] = (-0.9, 0.45),
    min_spacing_days: int = 25,
    discount_curve=None,
) -> list[ExpirySlice]:
    """Clean, extract (F, DF) and invert to implied vols, expiry by expiry.

    `otm_only` keeps the out-of-the-money leg at each strike. Both legs carry
    the same information once the forward is known, but the out-of-the-money
    one is the liquid side and, for American options, the side without an
    early-exercise premium. Weekly expiries inside a fortnight are dropped
    because their vols are dominated by the discreteness of the tick.

    `k_clip` bounds the log-moneyness that is calibrated at all. SPX lists puts
    down to k = -2.1, which is a strike 88% below the forward trading for a few
    cents, and those quotes dragged the worst slices out to 0.77 vol points of
    error and made one calendar constraint infeasible. They also do not matter
    here: the deepest barrier in the sample sits at 60% of initial, which is
    k = -0.51, so a band of [-1.2, 0.6] covers every level these notes can care
    about with room to spare. Local vol is held flat beyond the calibrated band
    rather than extrapolated from quotes that carry no information.
    """
    spot = chain.attrs["underlying_close"]
    clean = clean_quotes(chain, kind=kind, max_rel_spread=max_rel_spread)
    slices: list[ExpirySlice] = []

    # One series per expiry date. Where both an AM- and a PM-settled root list
    # the same date, the one with more surviving quotes is kept: that is the
    # series the volume is actually in, and the other would otherwise arrive as
    # a second slice at an almost identical T, which makes the calendar
    # comparison and dw/dT degenerate.
    chosen = (
        clean.groupby(["expiry", "root"]).size().rename("n").reset_index()
        .sort_values(["expiry", "n"], ascending=[True, False])
        .groupby("expiry").first()["root"].to_dict()
    )

    for (expiry, root), group in clean.groupby(["expiry", "root"]):
        if chosen.get(expiry) != root:
            continue
        t = (expiry - valuation_date).days / DAYS_PER_YEAR
        if t < min_t:
            continue
        override = None if discount_curve is None else float(discount_curve.discount(t)[0])
        try:
            forward, df, rmse, n = forward_and_discount(
                group, spot=spot, discount_override=override
            )
        except ValueError:
            continue

        rows = []
        for right, sub in group.groupby("right"):
            for strike, mid, spread in zip(sub["strike"], sub["mid"], sub["spread"]):
                if otm_only:
                    if right == "C" and strike < forward:
                        continue
                    if right == "P" and strike > forward:
                        continue
                if not k_clip[0] <= np.log(strike / forward) <= k_clip[1]:
                    continue
                iv = _implied_vol(mid / df, forward, strike, t, right)
                if not np.isfinite(iv) or iv <= 1e-4 or iv > 3.0:
                    continue
                rows.append((strike, iv, spread, mid))

        # A slice needs enough clean strikes to pin five parameters, but the
        # threshold cannot be flat. Short-dated expiries have hundreds of liquid
        # strikes, so a thin one there is a bad slice and should go: one DIA
        # expiry at 0.44 years survived on seven points and fitted to 3.6 vol
        # points of error. Long-dated expiries are thin for everyone, and they
        # are the only information about the long end - applying the same
        # threshold to them cut NDX coverage from 3.2 years to 1.2, which costs
        # far more than the noise it removes.
        if len(rows) < (10 if t < 1.5 else 6):
            continue

        rows.sort()
        strikes = np.array([r[0] for r in rows])
        ivs = np.array([r[1] for r in rows])
        spreads = np.array([r[2] for r in rows])
        mids = np.array([r[3] for r in rows])

        # A handful of strikes per expiry carry a crossed or stale quote that
        # survives the spread test but sits far off its neighbours. Comparing
        # each vol with the local median and cutting at a robust multiple of the
        # median absolute deviation removes those without touching the genuine
        # curvature of the smile.
        keep = _not_outlier(strikes, ivs)
        if keep.sum() >= 6:
            strikes, ivs, spreads, mids = strikes[keep], ivs[keep], spreads[keep], mids[keep]

        # Weight by quote tightness: a strike quoted 1% wide says far more about
        # where vol is than one quoted 40% wide. Capped so a single very tight
        # near-the-money strike cannot dominate the whole slice.
        rel = np.clip(spreads / np.maximum(mids, 1e-8), 1e-3, 10.0)
        weight = np.minimum(1.0 / rel, 50.0)

        slices.append(
            ExpirySlice(
                expiry=expiry,
                t=t,
                forward=forward,
                discount_factor=df,
                parity_rmse=rmse,
                parity_n=n,
                strikes=strikes,
                log_moneyness=np.log(strikes / forward),
                implied_vol=ivs,
                total_variance=ivs**2 * t,
                weight=weight,
                spread=spreads,
            )
        )

    slices.sort(key=lambda s: s.t)
    return _thin_expiries(slices, min_spacing_days)


def _thin_expiries(slices: list[ExpirySlice], min_spacing_days: int) -> list[ExpirySlice]:
    """Keep expiries spaced far enough apart that dw/dT means something.

    Dupire's numerator is dw/dT, and this project gets it by differencing
    adjacent fitted slices. SPX lists expiries three days apart, and each slice
    carries roughly 20 bp of independent fit error, so differencing two of them
    over dt = 0.008 years multiplies that noise by about 125. The result was a
    local-vol surface showing 63% vol across the put wing at five weeks on an
    index whose implied vols there are half that, with exact zeros and spikes
    elsewhere - all of it noise in the numerator rather than anything the market
    said.

    Thinning to monthly spacing cuts the amplification by roughly a factor of
    four and costs nothing real: a surface does not gain information from two
    expiries three days apart. The longest expiry is always kept, since it sets
    how far the surface reaches before extrapolation takes over.
    """
    if min_spacing_days <= 0 or len(slices) <= 2:
        return slices

    spacing = min_spacing_days / DAYS_PER_YEAR
    kept = [slices[0]]
    for sl in slices[1:-1]:
        if sl.t - kept[-1].t >= spacing:
            kept.append(sl)
    last = slices[-1]
    if last.t - kept[-1].t >= 0.5 * spacing:
        kept.append(last)
    elif len(kept) > 1:
        kept[-1] = last
    return kept


def parity_report(slices: list[ExpirySlice]) -> pd.DataFrame:
    """Per-expiry forward, discount factor and parity residual."""
    return pd.DataFrame(
        {
            "expiry": [s.expiry for s in slices],
            "t": [round(s.t, 4) for s in slices],
            "forward": [round(s.forward, 4) for s in slices],
            "discount_factor": [round(s.discount_factor, 6) for s in slices],
            "implied_zero_rate": [round(-np.log(s.discount_factor) / s.t, 6) for s in slices],
            "parity_rmse": [round(s.parity_rmse, 4) for s in slices],
            "parity_strikes": [s.parity_n for s in slices],
            "n_vol_points": [len(s.implied_vol) for s in slices],
        }
    )
