"""Does an ETF's option market agree with the cash index it tracks?

The Dow note (N2) references the Dow Jones Industrial Average, and DJX options
are too thin to calibrate a surface that reaches 2031: after cleaning, the
2026-10-09 snapshot leaves two usable DJX expiries, the furthest at 0.69 years.
DIA options on the same index reach 2.28 years with eleven. So the Dow surface
is fitted to DIA and the note is priced off it, which is a substitution that has
to be defended rather than asserted.

This module defends it by measuring. For each cash index that has an ETF
tracking it, both chains are cleaned the same way and their at-the-money implied
vols are compared at every expiry date both list. NDX against QQQ and RUT
against IWM are the controls: those two indices have deep enough option markets
that the project never needs the proxy, so any systematic gap between index and
ETF vols shows up there, where it can be seen, rather than hiding inside the one
substitution that matters.

Expiry thinning is switched off here. Thinning exists to keep dw/dT from
amplifying fit noise when slices sit three days apart, and it drops a different
subset from each chain, which would leave the two sides of this comparison
matched on almost nothing. The comparison needs breadth, not a usable dw/dT.

Two caveats are visible in the output rather than argued away. ETF options are
American, so their discount factor is taken from the Treasury curve instead of
fitted, for the reason in forwards.py. And an ETF's forward carries its own
dividend and borrow, so the index and ETF at-the-money points are the same
moneyness on two slightly different forwards.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import numpy as np

from ..data.rates import ZeroCurve, curve_for
from .forwards import ExpirySlice, build_slices, load_snapshot

# Index to the ETF that tracks it. DJX is the pair the project depends on; the
# other two are controls with no substitution behind them.
PROXY_PAIRS = (
    ("DJX", "DIA"),
    ("NDX", "QQQ"),
    ("RUT", "IWM"),
)


def _atm_vol(slice_: ExpirySlice) -> tuple[float, float]:
    """At-the-money implied vol, and the log-moneyness it was read at.

    Linear interpolation in log-moneyness across the two quotes bracketing
    k = 0, so the comparison is not at the mercy of which strikes happen to be
    listed. With no bracket, the nearest quote is used and its k is returned so
    the caller can see how far off the money it is.
    """
    k = slice_.log_moneyness
    vol = slice_.implied_vol
    order = np.argsort(k)
    k, vol = k[order], vol[order]
    if k[0] <= 0.0 <= k[-1]:
        return float(np.interp(0.0, k, vol)), 0.0
    nearest = int(np.argmin(np.abs(k)))
    return float(vol[nearest]), float(k[nearest])


def _slices(
    ticker: str, kind: str, snapshot_dir: Path, valuation_date: date,
    k_clip: tuple[float, float], curve: ZeroCurve,
) -> dict[date, ExpirySlice]:
    chain = load_snapshot(ticker, snapshot_dir)
    built = build_slices(
        chain,
        valuation_date=valuation_date,
        kind=kind,
        k_clip=k_clip,
        min_spacing_days=0,
        discount_curve=curve if kind == "etf" else None,
    )
    return {s.expiry: s for s in built}


def compare(
    snapshot_dir: Path, valuation_date: date, *,
    k_clip: tuple[float, float], treasury_csv: Path,
) -> list[dict]:
    """One row per index/ETF pair per shared expiry."""
    curve = curve_for(valuation_date, treasury_csv)
    rows: list[dict] = []

    for index_ticker, etf_ticker in PROXY_PAIRS:
        index_slices = _slices(index_ticker, "index", snapshot_dir,
                               valuation_date, k_clip, curve)
        etf_slices = _slices(etf_ticker, "etf", snapshot_dir,
                             valuation_date, k_clip, curve)
        shared = sorted(set(index_slices) & set(etf_slices))

        for expiry in shared:
            index_slice, etf_slice = index_slices[expiry], etf_slices[expiry]
            index_vol, index_k = _atm_vol(index_slice)
            etf_vol, etf_k = _atm_vol(etf_slice)
            rows.append({
                "index": index_ticker,
                "etf": etf_ticker,
                "expiry": expiry.isoformat(),
                "t_years": round(index_slice.t, 4),
                "index_atm_vol_pct": round(index_vol * 100, 3),
                "etf_atm_vol_pct": round(etf_vol * 100, 3),
                "difference_vol_pts": round((etf_vol - index_vol) * 100, 3),
                "index_quotes": len(index_slice.log_moneyness),
                "etf_quotes": len(etf_slice.log_moneyness),
                "index_read_at_k": round(index_k, 4),
                "etf_read_at_k": round(etf_k, 4),
            })
    return rows


def summarise(rows: list[dict]) -> list[dict]:
    """Per pair: how far the ETF's vol sits from the index's, and how far out."""
    out = []
    for index_ticker, etf_ticker in PROXY_PAIRS:
        pair = [r for r in rows if r["index"] == index_ticker]
        if not pair:
            out.append({"index": index_ticker, "etf": etf_ticker, "shared_expiries": 0})
            continue
        diffs = np.array([r["difference_vol_pts"] for r in pair])
        out.append({
            "index": index_ticker,
            "etf": etf_ticker,
            "shared_expiries": len(pair),
            "t_max_shared": max(r["t_years"] for r in pair),
            "mean_difference_vol_pts": round(float(diffs.mean()), 3),
            "median_abs_difference_vol_pts": round(float(np.median(np.abs(diffs))), 3),
            "max_abs_difference_vol_pts": round(float(np.abs(diffs).max()), 3),
        })
    return out


def run(repo_root: Path) -> tuple[list[dict], list[dict]]:
    import yaml

    cfg = yaml.safe_load((repo_root / "config/params.yaml").read_text())
    valuation_date = date.fromisoformat(str(cfg["snapshot"]["date"]))
    # option_chains points at the directory holding one folder per session.
    snapshot_dir = (repo_root / cfg["snapshot"]["option_chains"]
                    / valuation_date.isoformat())

    rows = compare(
        snapshot_dir, valuation_date,
        k_clip=tuple(cfg["surface"]["k_clip"]),
        treasury_csv=repo_root / cfg["snapshot"]["treasury_csv"],
    )
    summary = summarise(rows)

    processed = repo_root / "data/processed"
    processed.mkdir(parents=True, exist_ok=True)
    for name, data in (("proxy_check.csv", rows), ("proxy_summary.csv", summary)):
        if not data:
            continue
        target = processed / name
        with target.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
        print(f"wrote {target.relative_to(repo_root)} ({len(data)} rows)")

    for row in summary:
        if not row.get("shared_expiries"):
            print(f"  {row['index']}/{row['etf']}: no shared expiries after cleaning")
            continue
        print(f"  {row['index']}/{row['etf']}: {row['shared_expiries']} shared expiries "
              f"to {row['t_max_shared']:.2f}y, ETF minus index "
              f"{row['mean_difference_vol_pts']:+.3f} vol pts on average, "
              f"worst {row['max_abs_difference_vol_pts']:.3f}")
    return rows, summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    run(args.root)
