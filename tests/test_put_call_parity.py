"""Does the forward and discount factor extraction actually satisfy parity?

First item on the brief's validation checklist, and the one that catches the
most downstream damage: if the forward is wrong at an expiry, every implied vol
at that expiry is wrong by a consistent amount, the Dupire numerator inherits
it, and the error is almost impossible to attribute later.

The residual is checked in relative terms because the four underlyings here span
three orders of magnitude, from DIA at 517 to NDX at 30,883. A dollar of
residual means something very different on each.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.rates import curve_for
from src.vol.forwards import build_slices, forward_and_discount, load_snapshot

SNAPSHOT = ROOT / "data/raw/option_chains/2026-10-09"
VALUATION = date(2026, 10, 9)

pytestmark = pytest.mark.skipif(
    not SNAPSHOT.exists(), reason="option chain snapshot not present"
)


@pytest.mark.parametrize("ticker,kind", [("SPX", "index"), ("NDX", "index"), ("RUT", "index")])
def test_parity_residual_is_small_relative_to_spot(ticker, kind):
    """European index options: parity is an identity, so residuals must be tiny."""
    chain = load_snapshot(ticker, SNAPSHOT)
    slices = build_slices(chain, valuation_date=VALUATION, kind=kind)
    assert slices, f"no usable expiries for {ticker}"

    spot = chain.attrs["underlying_close"]
    relative = np.array([s.parity_rmse / spot for s in slices])
    # Five basis points of spot. The observed medians are far below this; the
    # threshold is set where a real problem would show rather than at the
    # current numbers, so the test keeps its power if the data changes.
    assert np.median(relative) < 5e-4, (
        f"{ticker}: median parity residual {np.median(relative):.2e} of spot"
    )
    assert relative.max() < 5e-3


@pytest.mark.parametrize("ticker", ["SPX", "NDX", "RUT"])
def test_implied_zero_rates_are_plausible(ticker):
    """The discount factors parity implies should look like a rate curve.

    Not compared tightly against Treasuries: the index option market discounts
    at its own funding rate and the short end is numerically fragile, since at
    two weeks the discount factor is within a thousandth of one and a tiny
    pricing error becomes a large implied rate. Past six months the two should
    be in the same territory, and a sign error or a factor of 100 would show
    here immediately.
    """
    chain = load_snapshot(ticker, SNAPSHOT)
    slices = [s for s in build_slices(chain, valuation_date=VALUATION, kind="index") if s.t > 0.5]
    assert slices

    rates = np.array([-np.log(s.discount_factor) / s.t for s in slices])
    assert np.all(rates > 0.0), f"{ticker}: non-positive implied zero rate"
    assert np.all(rates < 0.12), f"{ticker}: implausible implied zero rate {rates.max():.4f}"

    curve = curve_for(VALUATION, ROOT / "data/raw/treasury_par_yields_2026.csv")
    treasury = np.array([float(curve.zero_rate(s.t)[0]) for s in slices])
    assert np.median(np.abs(rates - treasury)) < 0.02


def test_discount_override_pins_the_discount_factor():
    """American options take their discount factor from the curve, not the slope.

    Fitting it freely on SMH returned a discount factor implying a 1.6% zero
    rate against 4.3% Treasuries, because the early-exercise premium on
    in-the-money puts grows with strike and tilts the regression. With the
    override the discount factor is exactly the curve's and only the forward is
    fitted.
    """
    chain = load_snapshot("SMH", SNAPSHOT)
    slices = build_slices(chain, valuation_date=VALUATION, kind="etf")
    group = None
    for expiry, candidate in chain.groupby("expiry"):
        if (expiry - VALUATION).days > 200:
            group = candidate
            break
    assert group is not None

    t = (group["expiry"].iloc[0] - VALUATION).days / 365.0
    curve = curve_for(VALUATION, ROOT / "data/raw/treasury_par_yields_2026.csv")
    pinned = float(curve.discount(t)[0])

    _, df_override, _, _ = forward_and_discount(
        group, spot=chain.attrs["underlying_close"], discount_override=pinned
    )
    assert df_override == pytest.approx(pinned, abs=1e-12)

    _, df_free, _, _ = forward_and_discount(group, spot=chain.attrs["underlying_close"])
    # The free fit should differ, which is the whole reason for the override.
    assert abs(df_free - pinned) > 1e-4

    assert slices, "SMH produced no usable expiries"


def test_one_settlement_series_per_expiry():
    """SPX lists AM- and PM-settled options on the same dates; only one is used.

    Merging them put two different instruments at the same strike into one
    slice and produced 62 vol points of fit error with eighteen calendar
    violations. Expiry dates must therefore be unique across the slice list.
    """
    chain = load_snapshot("SPX", SNAPSHOT)
    assert chain["root"].nunique() > 1, "expected both SPX and SPXW roots in the feed"

    slices = build_slices(chain, valuation_date=VALUATION, kind="index")
    expiries = [s.expiry for s in slices]
    assert len(expiries) == len(set(expiries))
