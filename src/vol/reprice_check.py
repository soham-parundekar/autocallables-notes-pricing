"""Does the local-vol Monte Carlo reprice the options it was calibrated to?

This is the gate. If a local-vol engine cannot return the vanilla prices that
built its own surface, nothing it says about an autocallable means anything, and
the brief is right that a failure here is almost always in the Dupire
derivatives or in the forward/discount step rather than in the Monte Carlo.

Two things are checked per expiry.

Forward reproduction: the simulated mean of S_T should equal the forward backed
out of put-call parity. This isolates the drift. If the carry is wrong, every
option at that expiry is biased in the same direction and the strike-by-strike
comparison below becomes hard to read.

Price inside the quoted spread: each listed strike's simulated price is compared
against its bid and ask. Mid-price agreement alone would be a weaker claim,
since a model can sit a long way from mid and still be inside a wide market, so
both are reported: the fraction of strikes inside the spread, and the error in
vol points against mid.

Monte Carlo error is reported alongside, because a "failure" that is smaller
than the standard error is not a failure of the surface.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..pricer.simulate import LocalVolGrid
from .forwards import build_slices, load_snapshot


def simulate_single_asset(
    grid: LocalVolGrid,
    spot: float,
    expiry_times: np.ndarray,
    *,
    n_paths: int = 400_000,
    steps_per_year: int = 252,
    seed: int = 20261009,
    antithetic: bool = True,
) -> np.ndarray:
    """Return simulated levels at each expiry: shape (paths, n_expiries)."""
    expiry_times = np.asarray(expiry_times, dtype=float)
    if antithetic:
        n_paths += n_paths % 2
        n_draw = n_paths // 2
    else:
        n_draw = n_paths

    t_end = float(expiry_times[-1])
    n_steps = max(int(round(t_end * steps_per_year)), 2 * expiry_times.size)
    times = np.unique(np.concatenate([np.linspace(0.0, t_end, n_steps + 1), expiry_times]))
    stops = np.searchsorted(times, expiry_times)

    rng = np.random.default_rng(seed)
    level = np.full(n_paths, float(spot))
    out = np.empty((n_paths, expiry_times.size), dtype=np.float64)
    next_stop = 0

    for step in range(1, times.size):
        t0, t1 = times[step - 1], times[step]
        dt = t1 - t0
        if dt <= 0:
            continue
        vol = grid(level, float(t0))
        carry = grid.surface.forward_curve.carry(float(t0))
        z = rng.standard_normal(n_draw)
        if antithetic:
            z = np.concatenate([z, -z])
        level = level * np.exp((carry - 0.5 * vol**2) * dt + vol * np.sqrt(dt) * z)

        while next_stop < expiry_times.size and step == stops[next_stop]:
            out[:, next_stop] = level
            next_stop += 1

    if next_stop != expiry_times.size:
        raise RuntimeError("did not reach every expiry")
    return out


def check_underlying(
    ticker: str,
    record: dict,
    surface,
    snapshot_dir: Path,
    valuation_date: date,
    *,
    kind: str = "index",
    k_clip: tuple[float, float] = (-1.2, 0.6),
    n_paths: int = 400_000,
    max_expiries: int | None = None,
    discount_curve=None,
) -> pd.DataFrame:
    """Reprice every clean listed option for one underlying."""
    chain = load_snapshot(record["chain_symbol"], snapshot_dir)
    slices = build_slices(
        chain, valuation_date=valuation_date, kind=kind,
        k_clip=k_clip, discount_curve=discount_curve,
    )
    if max_expiries:
        # Thin the expiry list evenly rather than truncating, so the check still
        # spans the whole term structure.
        keep = np.linspace(0, len(slices) - 1, max_expiries).round().astype(int)
        slices = [slices[i] for i in sorted(set(keep.tolist()))]

    times = np.array([s.t for s in slices])
    grid = LocalVolGrid(surface, t_max=float(times[-1]) * 1.02)
    levels = simulate_single_asset(grid, record["spot"], times, n_paths=n_paths)

    rows = []
    for j, sl in enumerate(slices):
        terminal = levels[:, j]
        mean_level = float(terminal.mean())
        for strike, iv, spread in zip(sl.strikes, sl.implied_vol, sl.spread):
            otm_call = strike >= sl.forward
            payoff = np.maximum(terminal - strike, 0.0) if otm_call else np.maximum(strike - terminal, 0.0)
            undiscounted = payoff.mean()
            model = sl.discount_factor * undiscounted
            stderr = sl.discount_factor * payoff.std(ddof=1) / np.sqrt(terminal.size)

            # Market mid is recovered from the cleaned implied vol so that the
            # comparison uses exactly the quote the surface was fitted to.
            import py_lets_be_rational as lbr
            market = sl.discount_factor * lbr.black(
                sl.forward, strike, iv, sl.t, 1 if otm_call else -1
            )
            bid, ask = market - spread / 2.0, market + spread / 2.0
            rows.append({
                "ticker": ticker,
                "expiry": sl.expiry,
                "t": sl.t,
                "strike": strike,
                "k": float(np.log(strike / sl.forward)),
                "right": "C" if otm_call else "P",
                "market": market,
                "bid": bid,
                "ask": ask,
                "model": model,
                "stderr": stderr,
                "inside_spread": bool(bid - stderr <= model <= ask + stderr),
                "error": model - market,
                "forward_market": sl.forward,
                "forward_simulated": mean_level,
            })
    return pd.DataFrame(rows)


def vol_point_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Repricing error and quoted spread expressed in implied-vol points.

    Dollar errors are not comparable across underlyings here: the same model
    accuracy looks very different on an index at 30,883 and a fund at 603. Vol
    points are, and they also explain the one result that looks like a failure.
    SPX's median quoted spread is about 0.08 vol points, roughly a twelfth of
    DJX's, so SPX can miss its spread while carrying one of the smallest
    absolute errors in the sample.
    """
    import py_lets_be_rational as lbr
    from scipy.stats import norm

    def vega(row) -> float:
        forward, strike, maturity = row.forward_market, row.strike, row.t
        price = max(row.market, 1e-12)
        try:
            iv = lbr.implied_volatility_from_a_transformed_rational_guess(
                price, forward, strike, maturity, 1 if row.right == "C" else -1
            )
        except Exception:
            return float("nan")
        if not np.isfinite(iv) or iv <= 0:
            return float("nan")
        sd = iv * np.sqrt(maturity)
        d1 = (np.log(forward / strike) + 0.5 * sd * sd) / sd
        return float(forward * np.sqrt(maturity) * norm.pdf(d1))

    work = frame.copy()
    work["vega"] = work.apply(vega, axis=1)
    work["err_volpts"] = (work["error"].abs() / work["vega"]) * 100.0
    work["spread_volpts"] = ((work["ask"] - work["bid"]) / work["vega"]) * 100.0

    rows = []
    for ticker, group in work.groupby("ticker"):
        usable = group[
            np.isfinite(group["err_volpts"])
            & np.isfinite(group["spread_volpts"])
            & (group["vega"] > 1e-6)
        ]
        rows.append({
            "ticker": ticker,
            "n": len(usable),
            "inside_pct": round(100.0 * group["inside_spread"].mean(), 1),
            "err_volpts_med": round(usable["err_volpts"].median(), 3),
            "err_volpts_p90": round(usable["err_volpts"].quantile(0.9), 3),
            "spread_volpts_med": round(usable["spread_volpts"].median(), 3),
            "ratio": round(
                usable["err_volpts"].median() / usable["spread_volpts"].median(), 2
            ),
        })
    return pd.DataFrame(rows)


def summarise(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-underlying pass rate and worst error, in price and vol terms."""
    out = []
    for ticker, group in frame.groupby("ticker"):
        fwd = group.drop_duplicates("expiry")
        out.append({
            "ticker": ticker,
            "options": len(group),
            "expiries": group["expiry"].nunique(),
            "inside_spread_pct": 100.0 * group["inside_spread"].mean(),
            "median_abs_error": group["error"].abs().median(),
            "max_abs_error": group["error"].abs().max(),
            "median_error_vs_spread": (group["error"].abs() / (group["ask"] - group["bid"])).median(),
            "forward_max_rel_error_bp": 1e4 * float(
                ((fwd["forward_simulated"] - fwd["forward_market"]).abs()
                 / fwd["forward_market"]).max()
            ),
        })
    return pd.DataFrame(out)


def run(repo_root: Path, *, n_paths: int = 200_000, max_expiries: int = 12) -> pd.DataFrame:
    """Reprice every underlying's listed options and write both summaries."""
    import json

    import yaml

    from ..data.rates import curve_for
    from .calibrate import load_surface

    cfg = yaml.safe_load((repo_root / "config/params.yaml").read_text())
    records = json.loads((repo_root / "data/processed/surfaces.json").read_text())
    valuation = cfg["snapshot"]["date"]
    if isinstance(valuation, str):
        valuation = datetime.fromisoformat(valuation).date()
    snapshot_dir = repo_root / cfg["snapshot"]["option_chains"] / valuation.isoformat()
    treasury = curve_for(valuation, repo_root / cfg["snapshot"]["treasury_csv"])
    k_clip = tuple(cfg["surface"]["k_clip"])

    frames = []
    for ticker, spec in cfg["underlyings"].items():
        surface = load_surface(records[ticker], k_bounds=k_clip)
        frames.append(
            check_underlying(
                ticker, records[ticker], surface, snapshot_dir, valuation,
                kind=spec["kind"], k_clip=k_clip, n_paths=n_paths,
                max_expiries=max_expiries,
                discount_curve=treasury if spec["exercise"] == "american" else None,
            )
        )
        print(f"  {ticker}: {len(frames[-1])} options repriced", flush=True)

    everything = pd.concat(frames, ignore_index=True)
    out = repo_root / "data/processed"
    everything.to_csv(out / "reprice_check.csv", index=False)
    summarise(everything).to_csv(out / "reprice_summary.csv", index=False)
    vol_point_summary(everything).to_csv(out / "reprice_volpoints.csv", index=False)

    print("\n=== repricing summary ===")
    print(summarise(everything).to_string(index=False))
    print("\n=== in implied-vol points ===")
    print(vol_point_summary(everything).to_string(index=False))
    return everything


if __name__ == "__main__":
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--paths", type=int, default=200_000)
    parser.add_argument("--max-expiries", type=int, default=12)
    args = parser.parse_args()
    run(args.root, n_paths=args.paths, max_expiries=args.max_expiries)
