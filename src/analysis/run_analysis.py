"""Produce every result in the report, in one reproducible pass.

Running this is the whole empirical project: it prices the six notes, inverts
for the funding spread, computes the Greeks and the three model-risk
comparisons, runs the convergence study, and writes machine-readable results to
data/processed/ for the notebook and the report to read.

Everything that can share a simulation does. Paths do not depend on the funding
spread or the coupon rate, so the spread inversion and the fair-coupon solve
reuse one simulation each rather than resimulating per Brent iteration.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..pricer.engine import build_controls, build_inputs, run_simulation, value
from ..pricer.payoff import price
from .funding_spread import fair_coupon, implied_spread, margin_decomposition, value_at_spread
from .greeks import bucketed_vega, correlation_sensitivity, economic_decomposition, spot_deltas
from .model_risk import extrapolation_comparison, flat_vol_comparison, timing_comparison

NOTE_ORDER = ["N1", "N2", "N3", "N4", "N5", "N6"]


def load_project(root: Path) -> tuple[dict, dict, dict]:
    cfg = yaml.safe_load((root / "config/params.yaml").read_text())
    notes = {n["id"]: n for n in yaml.safe_load((root / "config/notes.yaml").read_text())["notes"]}
    surfaces = json.loads((root / "data/processed/surfaces.json").read_text())
    return cfg, notes, surfaces


def corporate_oas(root: Path, as_of: date, series: str = "BAMLC0A3CA") -> dict:
    """Market credit spread benchmark for the issuers' senior unsecured debt.

    FINRA's TRACE is the brief's suggested source and would be better, being
    actual traded prints on each issuer's own bonds. Its free interface is
    interactive only and its API requires credentials, and the project does not
    bypass authentication, so the benchmark here is the ICE BofA A-rated US
    corporate index option-adjusted spread published through FRED. All four
    issuers' senior unsecured ratings sit in the A band, so the index is the
    right bucket, but it is an index rather than their own curve and the report
    says so.
    """
    frame = pd.read_csv(root / f"data/raw/fred_{series}.csv")
    frame.columns = ["observation_date", "oas"]
    frame["observation_date"] = pd.to_datetime(frame["observation_date"])
    frame["oas"] = pd.to_numeric(frame["oas"], errors="coerce")
    frame = frame.dropna()
    available = frame[frame["observation_date"] <= pd.Timestamp(as_of)]
    row = available.iloc[-1]
    return {
        "series": series,
        "description": "ICE BofA A-rated US corporate index, option-adjusted spread",
        "as_of": str(row["observation_date"].date()),
        "oas_pct": float(row["oas"]),
        "oas_bp": float(row["oas"]) * 100.0,
    }


def convergence_study(note_record, cfg, surfaces, root, *, path_counts, seed) -> list[dict]:
    """Price and standard error against path count, to show the estimate settled."""
    out = []
    for n in path_counts:
        started = time.time()
        result = value(note_record, cfg, surfaces, root, n_paths=n, seed=seed)
        out.append({
            "paths": n,
            "value": result["value"],
            "stderr": result["stderr"],
            "prob_called": result["prob_called"],
            "seconds": round(time.time() - started, 1),
        })
    return out


def analyse_note(note_id, note_record, cfg, surfaces, root, *, snapshot_date) -> dict:
    """Everything the report says about one note."""
    mc = cfg["monte_carlo"]
    seed = mc["seed"]
    full = mc["paths"]
    risk_paths = max(full // 4, 20000)

    base = value(note_record, cfg, surfaces, root, n_paths=full, seed=seed)
    margin = margin_decomposition(note_record)

    # One simulation, reused for both spread inversions and for V(s) curves.
    inputs = build_inputs(note_record, cfg, surfaces, root)
    sim = run_simulation(inputs, cfg, n_paths=full, seed=seed)
    controls = build_controls(inputs, sim) if cfg["monte_carlo"].get("control_variate") else None
    bracket = tuple(cfg["funding"]["spread_bracket"])
    spread_optimal = implied_spread(inputs, sim, margin["disclosed_estimated_value"],
                                    bracket=bracket, tolerance=cfg["funding"]["tolerance"],
                                    controls=controls)
    value_curve = [
        {"spread_bp": s * 1e4, "value": value_at_spread(inputs, sim, s, controls)}
        for s in (-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.05, 0.10)
    ]

    # The no-call diagnostic is only meaningful where the call is the issuer's
    # discretion. For the auto-callable note redemption is mechanical and
    # contractual, so removing it describes a different security entirely.
    spread_no_call = None
    if note_record["call"]["style"] != "automatic":
        no_call_inputs = build_inputs(note_record, cfg, surfaces, root, disable_call=True)
        no_call_sim = run_simulation(no_call_inputs, cfg, n_paths=full, seed=seed)
        no_call_controls = (
            build_controls(no_call_inputs, no_call_sim)
            if cfg["monte_carlo"].get("control_variate") else None
        )
        spread_no_call = implied_spread(
            no_call_inputs, no_call_sim, margin["disclosed_estimated_value"],
            bracket=bracket, tolerance=cfg["funding"]["tolerance"],
            controls=no_call_controls,
        )
        spread_no_call["value_at_zero_spread"] = value_at_spread(
            no_call_inputs, no_call_sim, 0.0, no_call_controls
        )

    oas = corporate_oas(root, inputs.terms.pricing_date)
    coupon = fair_coupon(note_record, cfg, surfaces, root,
                         credit_spread=oas["oas_bp"] / 1e4, n_paths=risk_paths, seed=seed)

    deltas = spot_deltas(note_record, cfg, surfaces, root,
                         n_paths=risk_paths, seed=seed, bump=cfg["greeks"]["spot_bump"])
    base_risk = value(note_record, cfg, surfaces, root, n_paths=risk_paths, seed=seed)["value"]
    for row in deltas:
        row["gamma_per_1pct_squared"] = row["gamma_per_1pct_squared"] - 2.0 * base_risk

    vega = bucketed_vega(note_record, cfg, surfaces, root, n_paths=risk_paths, seed=seed,
                         buckets=cfg["greeks"]["vega_buckets"],
                         bump=cfg["greeks"]["vol_bump"], base_value=base_risk)
    correlation = correlation_sensitivity(note_record, cfg, surfaces, root,
                                          n_paths=risk_paths, seed=seed,
                                          shift=cfg["greeks"]["correlation_bump"])
    decomposition = economic_decomposition(note_record, cfg, surfaces, root,
                                           n_paths=risk_paths, seed=seed)
    flat = flat_vol_comparison(note_record, cfg, surfaces, root,
                               n_paths=risk_paths, seed=seed, base_value=base_risk)
    extrapolation = extrapolation_comparison(note_record, cfg, surfaces, root,
                                             n_paths=risk_paths, seed=seed, base_value=base_risk)
    timing = timing_comparison(note_record, cfg, surfaces, root,
                               n_paths=risk_paths, seed=seed, base_value=base_risk,
                               snapshot_date=snapshot_date)

    return {
        "note_id": note_id,
        "base": base,
        "margin": margin,
        "implied_spread_optimal_exercise": spread_optimal,
        "implied_spread_no_early_call": spread_no_call,
        "value_vs_spread": value_curve,
        "corporate_oas_benchmark": oas,
        "fair_coupon": coupon,
        "deltas": deltas,
        "bucketed_vega": vega,
        "correlation": correlation,
        "decomposition": decomposition,
        "flat_vol": flat,
        "extrapolation": extrapolation,
        "timing": timing,
        "risk_paths": risk_paths,
    }


def main(
    root: Path,
    *,
    note_ids: list[str] | None = None,
    resume: bool = True,
    with_convergence: bool = True,
    out_name: str = "analysis.json",
) -> dict:
    """Run the analysis, saving after each note so a long run can be resumed.

    The whole sweep takes about an hour: six notes, each needing roughly
    eighteen simulations for the base value, the two spread inversions, the
    deltas, the vega buckets, the correlation bump and the three model-risk
    comparisons, and the five-year notes carry 1,266 daily steps against 60
    observation dates. Writing after every note means an interruption costs one
    note rather than the lot.
    """
    cfg, notes, surfaces = load_project(root)
    snapshot = cfg["snapshot"]["date"]
    if isinstance(snapshot, str):
        snapshot = datetime.fromisoformat(snapshot).date()

    target = root / "data/processed" / out_name
    payload = {"generated_for_snapshot": snapshot.isoformat(), "notes": {}, "convergence": {}}
    if resume and target.exists():
        payload = json.loads(target.read_text())
        payload.setdefault("notes", {})
        payload.setdefault("convergence", {})

    results = payload["notes"]
    for note_id in (note_ids or NOTE_ORDER):
        if resume and note_id in results:
            print(f"  {note_id}  already done, skipping")
            continue
        started = time.time()
        results[note_id] = analyse_note(note_id, notes[note_id], cfg, surfaces, root,
                                        snapshot_date=snapshot)
        base = results[note_id]["base"]
        ev = results[note_id]["margin"]["disclosed_estimated_value"]
        print(
            f"  {note_id}  model={base['value']:>8.2f} (se {base['stderr']:.2f})  "
            f"disclosed={ev:>7.2f}  gap={base['value'] - ev:>+7.2f}  "
            f"[{time.time() - started:.0f}s]",
            flush=True,
        )
        target.write_text(json.dumps(payload, indent=1, default=str))

    if with_convergence:
        for note_id in ("N4", "N1"):
            if resume and note_id in payload["convergence"]:
                continue
            payload["convergence"][note_id] = convergence_study(
                notes[note_id], cfg, surfaces, root,
                path_counts=cfg["monte_carlo"]["convergence_path_counts"],
                seed=cfg["monte_carlo"]["seed"],
            )
            print(f"  convergence {note_id} done", flush=True)
            target.write_text(json.dumps(payload, indent=1, default=str))

    target.write_text(json.dumps(payload, indent=1, default=str))
    print(f"\nwrote {target.relative_to(root)}")
    return payload


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the full note analysis.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--notes", nargs="*", default=None)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--no-convergence", action="store_true")
    parser.add_argument(
        "--out", default="analysis.json",
        help="filename under data/processed/; use a separate file to run notes in parallel",
    )
    args = parser.parse_args()
    main(args.root, note_ids=args.notes, resume=not args.no_resume,
         with_convergence=not args.no_convergence, out_name=args.out)
