"""Calibrate every underlying's surface once and cache the result.

The SPX fit takes about ninety seconds because it has forty-six expiries and
roughly nine thousand clean quotes, and nothing downstream should pay that cost
again. This module runs the whole Module B chain - clean, forwards, implied
vols, SVI, arbitrage checks - and writes the fitted parameters to
data/processed/ as JSON, so the pricing and risk runs load five numbers per
expiry instead of refitting.

JSON rather than a pickle on purpose: a reviewer can open it, the diff is
readable, and it does not pin the project to a Python version.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..data.rates import curve_for
from .forwards import build_slices, load_snapshot, parity_report
from .local_vol import ForwardCurve, LocalVolSurface
from .svi import SVIParams, audit, fit_surface


def calibrate_underlying(
    ticker: str,
    spec: dict,
    cfg: dict,
    *,
    valuation_date: date,
    repo_root: Path,
) -> dict:
    """Fit one underlying and return its parameters plus the diagnostics."""
    snapshot_dir = repo_root / cfg["snapshot"]["option_chains"] / valuation_date.isoformat()
    chain = load_snapshot(spec["chain"], snapshot_dir)

    # American options get their discount factor from the Treasury curve rather
    # than from the parity slope; see forwards.forward_and_discount.
    discount_curve = None
    if spec["exercise"] == "american":
        discount_curve = curve_for(valuation_date, repo_root / cfg["snapshot"]["treasury_csv"])

    surf = cfg["surface"]
    slices = build_slices(
        chain,
        valuation_date=valuation_date,
        kind=spec["kind"],
        min_t=surf["min_days_to_expiry"] / 365.0,
        max_rel_spread=surf["max_relative_spread"],
        k_clip=tuple(surf["k_clip"]),
        min_spacing_days=surf.get("min_expiry_spacing_days", 25),
        discount_curve=discount_curve,
    )
    if not slices:
        raise RuntimeError(f"{ticker}: no usable expiries in {snapshot_dir}")

    params, report = fit_surface(slices, calendar_band=tuple(surf["k_clip"]))
    checks = audit(params, report)

    return {
        "ticker": ticker,
        "chain_symbol": spec["chain"],
        "kind": spec["kind"],
        "exercise": spec["exercise"],
        "proxy_for": spec.get("proxy_for"),
        "valuation_date": valuation_date.isoformat(),
        "spot": float(chain.attrs["underlying_close"]),
        "slices": [
            {
                "expiry": r["expiry"].isoformat(),
                "t": r["t"],
                "forward": r["forward"],
                "discount_factor": r["discount_factor"],
                "a": p.a, "b": p.b, "rho": p.rho, "m": p.m, "sigma": p.sigma,
                "n_points": r["n_points"],
                "rmse_vol": r["rmse_vol"],
                "parity_rmse": r["parity_rmse"],
                "butterfly_min": r["butterfly_min"],
                "calendar_min_gap": r["calendar_min_gap"],
                "constrained": bool(r["constrained"]),
                "grid_k_min": r["grid_k_min"],
                "grid_k_max": r["grid_k_max"],
            }
            for p, r in zip(params, report)
        ],
        "audit": checks,
        "parity": _jsonable(parity_report(slices)),
    }


def _jsonable(frame: pd.DataFrame) -> dict:
    """Column dict with dates as ISO strings, so the cache stays plain JSON."""
    out = {}
    for column in frame.columns:
        values = frame[column].tolist()
        out[column] = [v.isoformat() if isinstance(v, date) else v for v in values]
    return out


def load_surface(
    record: dict,
    *,
    extrapolation: str = "frozen_smile",
    tail_vol_shift: float = 0.0,
    k_bounds: tuple[float, float] = (-0.9, 0.45),
    bucket_shift: tuple[float, float, float] | None = None,
) -> LocalVolSurface:
    """Rebuild a LocalVolSurface from a cached calibration record.

    `bucket_shift` is (t_low, t_high, delta) and adds delta to the implied vol
    of every slice whose maturity falls in the bucket, which is how the
    maturity-bucketed vega is produced. It is applied by scaling each slice's
    total variance by ((sigma_atm + delta)/sigma_atm)^2, which shifts vol by
    exactly delta at the money and very close to it across the smile. An exact
    per-strike shift cannot be written as a raw SVI slice, and the difference is
    far below the size of the vega being measured.
    """
    params = []
    for s in record["slices"]:
        a, b = s["a"], s["b"]
        if bucket_shift is not None:
            t_low, t_high, delta = bucket_shift
            if t_low <= s["t"] < t_high and delta:
                probe = SVIParams(a=a, b=b, rho=s["rho"], m=s["m"], sigma=s["sigma"], t=s["t"])
                atm_vol = float(probe.implied_vol(np.array([0.0]))[0])
                if atm_vol > 1e-6:
                    scale = ((atm_vol + delta) / atm_vol) ** 2
                    a, b = a * scale, b * scale
        params.append(
            SVIParams(a=a, b=b, rho=s["rho"], m=s["m"], sigma=s["sigma"], t=s["t"])
        )
    curve = ForwardCurve(
        times=np.array([s["t"] for s in record["slices"]]),
        forwards=np.array([s["forward"] for s in record["slices"]]),
        spot=record["spot"],
    )
    return LocalVolSurface(
        params, curve, ticker=record["ticker"],
        extrapolation=extrapolation, tail_vol_shift=tail_vol_shift, k_bounds=k_bounds,
    )


def run(repo_root: Path, *, tickers: list[str] | None = None) -> dict:
    cfg = yaml.safe_load((repo_root / "config/params.yaml").read_text())
    valuation_date = cfg["snapshot"]["date"]
    if isinstance(valuation_date, str):
        valuation_date = datetime.fromisoformat(valuation_date).date()

    wanted = tickers or list(cfg["underlyings"])
    out = {}
    for ticker in wanted:
        record = calibrate_underlying(
            ticker, cfg["underlyings"][ticker], cfg,
            valuation_date=valuation_date, repo_root=repo_root,
        )
        out[ticker] = record
        a = record["audit"]
        rmse = np.array([s["rmse_vol"] for s in record["slices"]])
        print(
            f"  {ticker:<4} from {record['chain_symbol']:<4} "
            f"slices={a['n_slices']:>3}  maxT={max(s['t'] for s in record['slices']):>5.2f}y  "
            f"rmse_vol med/max={np.median(rmse)*1e4:>5.1f}/{rmse.max()*1e4:>6.1f}bp  "
            f"butterfly_fails={a['butterfly_failures']}  calendar_fails={a['calendar_failures']}"
        )

    target = repo_root / "data/processed/surfaces.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {target.relative_to(repo_root)}")
    return out


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fit and cache every underlying's SVI surface.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--tickers", nargs="*", default=None)
    args = parser.parse_args()
    run(args.root, tickers=args.tickers)
