"""How much of its own call does the disclosed estimated value appear to charge for?

The funding-spread inversion in `funding_spread.py` returns a negative spread
for every note in this sample, or no solution at all, which is not a funding
rate. Rather than report an impossible number, this module turns the question
around.

Hold the funding spread at something defensible - the issuer's own traded bond
spread, proxied by the ICE BofA A-rated corporate index - and ask how much of
the issuer's call the disclosed value is consistent with deducting:

    EV_disclosed = V_no_call(s) - phi * [ V_no_call(s) - V_optimal(s) ]

so that

    phi = [ V_no_call(s) - EV_disclosed ] / [ V_no_call(s) - V_optimal(s) ]

phi = 1 means the disclosed value charges the full rational value of the call
the issuer holds. phi = 0 means it charges none of it. Values in between say the
disclosed number sits between a note valued as if it ran to maturity and one
valued on the assumption the issuer exercises optimally.

This is an identification exercise, not a claim about anyone's model. phi
absorbs every difference between this model and the issuer's: correlation marked
above realised, a different volatility surface, a different funding assumption,
and any call policy short of optimal all land in the same number. What it does
establish is the size of the thing that has to differ, measured in the one unit
that moves the value by enough to matter. The report says so plainly rather than
presenting phi as a measurement of issuer behaviour.

The auto-callable note is excluded, and for an instructive reason: its
redemption is mechanical, and removing it *lowers* the investor's value, because
redeeming at par in a good state protects the investor from a later knock-in. An
automatic call at 93.25% is worth something to the holder, not to the issuer, so
there is no call cost to attribute.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import yaml

from ..pricer.engine import build_controls, build_inputs, run_simulation
from .funding_spread import value_at_spread


def attribute(
    note_record: dict,
    cfg: dict,
    surface_records: dict,
    repo_root: Path,
    *,
    funding_spread: float,
    n_paths: int,
    seed: int,
) -> dict:
    """Value the note at one spread with and without the call, and solve for phi."""
    disclosed = float(note_record["disclosed_estimated_value"])
    use_controls = bool(cfg["monte_carlo"].get("control_variate"))

    optimal = build_inputs(note_record, cfg, surface_records, repo_root)
    optimal_sim = run_simulation(optimal, cfg, n_paths=n_paths, seed=seed)
    optimal_controls = build_controls(optimal, optimal_sim) if use_controls else None
    value_optimal = value_at_spread(optimal, optimal_sim, funding_spread, optimal_controls)

    if note_record["call"]["style"] == "automatic":
        return {
            "note_id": note_record["id"],
            "applicable": False,
            "reason": "redemption is automatic and contractual; it benefits the holder",
            "funding_spread_bp": funding_spread * 1e4,
            "value_optimal": value_optimal,
            "disclosed_estimated_value": disclosed,
        }

    no_call = build_inputs(note_record, cfg, surface_records, repo_root, disable_call=True)
    no_call_sim = run_simulation(no_call, cfg, n_paths=n_paths, seed=seed)
    no_call_controls = build_controls(no_call, no_call_sim) if use_controls else None
    value_no_call = value_at_spread(no_call, no_call_sim, funding_spread, no_call_controls)

    call_value = value_no_call - value_optimal
    phi = (value_no_call - disclosed) / call_value if abs(call_value) > 1e-9 else float("nan")

    return {
        "note_id": note_record["id"],
        "applicable": True,
        "funding_spread_bp": funding_spread * 1e4,
        "value_no_call": value_no_call,
        "value_optimal": value_optimal,
        "call_value": call_value,
        "disclosed_estimated_value": disclosed,
        "implied_call_fraction": phi,
        # What the disclosed value leaves unexplained once the full call is
        # deducted at this spread. Positive means the disclosed value is above
        # even the no-call valuation, which no call policy can produce.
        "residual_vs_full_call": disclosed - value_optimal,
        "residual_vs_no_call": disclosed - value_no_call,
    }


def main(repo_root: Path, *, note_ids: list[str] | None = None) -> list[dict]:
    cfg = yaml.safe_load((repo_root / "config/params.yaml").read_text())
    notes = {n["id"]: n for n in
             yaml.safe_load((repo_root / "config/notes.yaml").read_text())["notes"]}
    surfaces = json.loads((repo_root / "data/processed/surfaces.json").read_text())

    # The benchmark spread is the A-rated corporate OAS on each note's own
    # pricing date, not one number for the sample. N5 priced two days before
    # the rest, when the index stood three basis points wider, and holding
    # every note at one date's spread would quietly misprice that one.
    from .results import load_results

    saved, _ = load_results(repo_root)
    benchmarks = {
        note_id: float(result["corporate_oas_benchmark"]["oas_bp"])
        for note_id, result in saved.items()
        if result.get("corporate_oas_benchmark")
    }
    if not benchmarks:
        raise SystemExit(
            "no priced notes found; run src.analysis.run_analysis before this"
        )

    paths = max(cfg["monte_carlo"]["paths"] // 4, 20000)
    out = []
    for note_id in (note_ids or sorted(notes)):
        if note_id not in benchmarks:
            print(f"  {note_id}  skipped: not priced yet, so it has no benchmark spread")
            continue
        row = attribute(
            notes[note_id], cfg, surfaces, repo_root,
            funding_spread=benchmarks[note_id] / 1e4, n_paths=paths,
            seed=cfg["monte_carlo"]["seed"],
        )
        out.append(row)
        if row["applicable"]:
            print(
                f"  {note_id}  no-call {row['value_no_call']:>8.2f}  "
                f"optimal {row['value_optimal']:>8.2f}  call worth {row['call_value']:>6.2f}  "
                f"disclosed {row['disclosed_estimated_value']:>7.2f}  "
                f"phi {row['implied_call_fraction']:>6.2f}", flush=True
            )
        else:
            print(f"  {note_id}  not applicable: {row['reason']}", flush=True)

    target = repo_root / "data/processed/call_attribution.json"
    used = sorted({row["funding_spread_bp"] for row in out})
    target.write_text(json.dumps({
        # Kept for readers who want one number; the per-note spread on each row
        # is the one actually used.
        "funding_spread_bp": used[0] if len(used) == 1 else used,
        "funding_spread_basis": "A-rated corporate OAS on each note's pricing date",
        "notes": out,
    }, indent=1, default=str))
    print(f"\nwrote {target.relative_to(repo_root)}")
    return out


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--notes", nargs="*", default=None)
    args = parser.parse_args()
    main(args.root, note_ids=args.notes)
