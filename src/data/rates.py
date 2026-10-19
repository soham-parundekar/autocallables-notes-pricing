"""Treasury par-yield curve, bootstrapped to the discount factors used for discounting.

This is the project's risk-free curve, and calling it that needs a caveat the
report repeats: a real dealer discounts a structured note off a SOFR OIS curve,
and there is no free source of OIS swap quotes. The daily Treasury par yield
curve is public and official, so it stands in. The two differ by a spread of
roughly a handful of basis points at these tenors, which is small next to the
funding spreads Module E is trying to measure (hundreds of basis points) but is
not nothing, and it is a documented approximation rather than a hidden one.

Par yields are bootstrapped to zero rates rather than used directly. At the
2-year point the difference is a basis point or two; by 30 years it is large
enough to matter, and the five-year notes in this sample sit far enough out that
treating par yields as zeros would be sloppy.

The curve is also what fixes the discount factor for the American ETF options.
src/vol/forwards.py backs out both the forward and the discount factor from
put-call parity, which is exact for the European index options but biased for
American ones: the early-exercise premium on in-the-money puts grows with
strike, tilting the regression slope and handing back a discount factor that
implied a 1.6% zero rate for SMH against 4.3% Treasuries. For those names the
discount factor comes from here and only the forward is fitted.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

TREASURY_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)

# Column label in the Treasury file -> tenor in years.
PAR_TENORS = {
    "1 Mo": 1 / 12, "1.5 Month": 1.5 / 12, "2 Mo": 2 / 12, "3 Mo": 0.25,
    "4 Mo": 4 / 12, "6 Mo": 0.5, "1 Yr": 1.0, "2 Yr": 2.0, "3 Yr": 3.0,
    "5 Yr": 5.0, "7 Yr": 7.0, "10 Yr": 10.0, "20 Yr": 20.0, "30 Yr": 30.0,
}

# Treasury notes pay semi-annually, so the par bond that defines each quoted
# yield has two coupons a year.
COUPONS_PER_YEAR = 2


class ZeroCurve:
    """Continuously compounded zero curve, linear in rate between pillars.

    Linear-in-zero-rate is chosen over linear-in-discount-factor because it
    keeps the implied forward rates finite and reasonably behaved, which is what
    the Monte Carlo drift actually consumes.
    """

    def __init__(self, tenors: np.ndarray, zeros: np.ndarray, as_of: date, source: str):
        order = np.argsort(tenors)
        self.tenors = np.asarray(tenors, dtype=float)[order]
        self.zeros = np.asarray(zeros, dtype=float)[order]
        self.as_of = as_of
        self.source = source

    def zero_rate(self, t: float | np.ndarray) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, dtype=float))
        # Flat extrapolation at both ends. Beyond 30 years is irrelevant here,
        # and below 1 month the curve is flat for all practical purposes.
        return np.interp(t, self.tenors, self.zeros)

    def discount(self, t: float | np.ndarray) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, dtype=float))
        return np.exp(-self.zero_rate(t) * t)

    def forward_rate(self, t0: float, t1: float) -> float:
        """Continuously compounded forward between two times."""
        if t1 <= t0:
            raise ValueError("t1 must exceed t0")
        d0, d1 = float(self.discount(t0)[0]), float(self.discount(t1)[0])
        return float(np.log(d0 / d1) / (t1 - t0))

    def __repr__(self) -> str:
        pillars = ", ".join(f"{t:g}y={r:.4%}" for t, r in zip(self.tenors, self.zeros))
        return f"ZeroCurve(as_of={self.as_of}, {pillars})"


def load_treasury_par(csv_path: Path) -> pd.DataFrame:
    """Read a Treasury daily-yield CSV, indexed by date, yields as decimals."""
    raw = pd.read_csv(csv_path)
    raw["Date"] = pd.to_datetime(raw["Date"], format="%m/%d/%Y")
    cols = [c for c in raw.columns if c in PAR_TENORS]
    out = raw.set_index("Date")[cols] / 100.0
    return out.sort_index()


def bootstrap_zero_curve(par_yields: pd.Series, as_of: date, source: str) -> ZeroCurve:
    """Bootstrap continuously compounded zeros from semi-annual par yields.

    Tenors of a year or less are quoted on a discount/simple basis and are
    treated as single-payment instruments. Past a year the quoted yield belongs
    to a par bond paying twice a year, so each pillar is solved so that the par
    bond prices to 100 given the already-solved shorter pillars.
    """
    tenors, zeros = [], []

    for label, t in sorted(PAR_TENORS.items(), key=lambda kv: kv[1]):
        if label not in par_yields.index or not np.isfinite(par_yields[label]):
            continue
        y = float(par_yields[label])

        if t <= 1.0:
            # Treat as a single cashflow: 1 = (1 + y*t) * DF  ->  z = ln(1+y t)/t
            z = np.log1p(y * t) / t
        else:
            curve = ZeroCurve(np.array(tenors), np.array(zeros), as_of, source)
            times = np.arange(1, int(round(t * COUPONS_PER_YEAR)) + 1) / COUPONS_PER_YEAR
            coupon = y / COUPONS_PER_YEAR
            known = times[times < tenors[-1] + 1e-12] if tenors else np.array([])
            pv_known = float(np.sum(coupon * curve.discount(known))) if known.size else 0.0

            # Everything from the last solved pillar out to t is discounted at a
            # single unknown zero rate, found by forcing the par bond to 100.
            unknown = times[times >= (tenors[-1] if tenors else 0.0) - 1e-12]
            unknown = unknown[unknown > (tenors[-1] if tenors else 0.0) + 1e-12]

            def par_price(z_trial: float) -> float:
                blended = ZeroCurve(
                    np.append(tenors, t), np.append(zeros, z_trial), as_of, source
                )
                pv = pv_known + float(np.sum(coupon * blended.discount(unknown)))
                return pv + float(blended.discount(t)[0]) - 1.0

            lo, hi = -0.02, 0.30
            for _ in range(200):
                mid = 0.5 * (lo + hi)
                if par_price(mid) > 0:
                    lo = mid
                else:
                    hi = mid
            z = 0.5 * (lo + hi)

        tenors.append(t)
        zeros.append(z)

    return ZeroCurve(np.array(tenors), np.array(zeros), as_of, source)


def curve_for(as_of: date, csv_path: Path) -> ZeroCurve:
    """Bootstrap the curve for one date, using the latest quote at or before it."""
    par = load_treasury_par(csv_path)
    stamp = pd.Timestamp(as_of)
    available = par.index[par.index <= stamp]
    if available.empty:
        raise ValueError(f"no Treasury quote on or before {as_of} in {csv_path}")
    row = par.loc[available[-1]]
    return bootstrap_zero_curve(
        row, available[-1].date(), f"US Treasury daily par yield curve, {available[-1].date()}"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Show the bootstrapped Treasury zero curve.")
    parser.add_argument("--csv", type=Path, default=Path("data/raw/treasury_par_yields_2026.csv"))
    parser.add_argument("--date", type=str, default="2026-10-09")
    args = parser.parse_args()

    curve = curve_for(datetime.fromisoformat(args.date).date(), args.csv)
    print(curve)
    print("\n   t      zero (cc)   discount")
    for t in (0.25, 0.5, 1, 1.75, 1.93, 2, 3, 5, 7, 10):
        print(f"  {t:>5.2f}  {curve.zero_rate(t)[0]:>10.4%}  {curve.discount(t)[0]:>10.6f}")

    # Repricing the par bonds the curve was built from is the one check that
    # catches a bootstrap error; every quoted tenor should come back at 100.
    par = load_treasury_par(args.csv)
    row = par.loc[par.index[par.index <= pd.Timestamp(args.date)][-1]]
    print("\n  par-bond reprice check (should be 100.000000)")
    worst = 0.0
    for label, t in sorted(PAR_TENORS.items(), key=lambda kv: kv[1]):
        if label not in row.index or not np.isfinite(row[label]) or t <= 1.0:
            continue
        times = np.arange(1, int(round(t * COUPONS_PER_YEAR)) + 1) / COUPONS_PER_YEAR
        coupon = float(row[label]) / COUPONS_PER_YEAR
        price = 100.0 * (coupon * curve.discount(times).sum() + curve.discount(t)[0])
        worst = max(worst, abs(price - 100.0))
        print(f"  {label:>6}  coupon={float(row[label]):>7.4%}  price={price:>12.6f}")
    print(f"  worst absolute error: {worst:.2e}")
