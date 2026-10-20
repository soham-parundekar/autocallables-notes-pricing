"""Where does an issuer-callable note's value curve meet its disclosed value?

The headline inversion searches a funding spread in [-300, +1500] basis points,
which is the range any defensible funding assumption lives in. One note in the
sample, the Goldman note, has no solution there: at the bottom of the bracket
its model value is still below the value its issuer disclosed.

That supports a narrow statement and not a broad one. "No solution inside the
searched bracket" is what the run establishes; "unreachable at any funding
rate" is a different and stronger claim, and the curve does not support it. The
value is monotone decreasing in the spread and the last step before the bracket
ends is worth several dollars, so a sufficiently negative spread reaches the
disclosed value. The interesting quantity is how negative it has to be.

This module answers that by evaluating the same note on the same paths over a
deliberately implausible range of spreads and solving for the crossing. The
answer is not a funding spread anyone would defend; it is the number that lets
the paper say exactly how far outside economic plausibility the disclosed value
sits, rather than overstating the result as impossibility.

Nothing here re-prices the note or changes the model. One simulation is run and
every spread is applied to the same cashflows, so the curve is the discounting
identity evaluated at more points than the headline run used.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import brentq

from ..pricer.engine import build_controls, build_inputs, run_simulation
from .funding_spread import value_at_spread


def extended_curve(
    note_record: dict, cfg: dict, surface_records: dict, repo_root: Path,
    *, spreads_bp: list[float], n_paths: int | None = None, seed: int | None = None,
) -> dict:
    """Value one note across an extended spread grid, on a single path set."""
    inputs = build_inputs(note_record, cfg, surface_records, repo_root)
    sim = run_simulation(inputs, cfg, n_paths=n_paths, seed=seed)
    controls = build_controls(inputs, sim) if cfg["monte_carlo"].get("control_variate") else None

    curve = [
        {"spread_bp": float(bp), "value": value_at_spread(inputs, sim, bp / 1e4, controls)}
        for bp in sorted(spreads_bp)
    ]
    disclosed = float(note_record["disclosed_estimated_value"])
    values = [point["value"] for point in curve]

    out = {
        "note_id": note_record["id"],
        "disclosed_estimated_value": disclosed,
        "paths": int(n_paths or cfg["monte_carlo"]["paths"]),
        "seed": int(seed if seed is not None else cfg["monte_carlo"]["seed"]),
        "headline_bracket_bp": [b * 1e4 for b in cfg["funding"]["spread_bracket"]],
        "curve": curve,
        "monotone_decreasing": all(
            values[i] >= values[i + 1] for i in range(len(values) - 1)
        ),
    }

    # Solve for the crossing where the grid brackets it. Values fall as the
    # spread rises, so the root sits between the last point above the disclosed
    # value and the first point below it.
    crossing = None
    for lower, upper in zip(curve, curve[1:]):
        if (lower["value"] - disclosed) * (upper["value"] - disclosed) <= 0:
            crossing = brentq(
                lambda bp: value_at_spread(inputs, sim, bp / 1e4, controls) - disclosed,
                lower["spread_bp"], upper["spread_bp"], xtol=1e-4,
            )
            break

    out["crossing_bp"] = float(crossing) if crossing is not None else None
    out["reached_within_extended_grid"] = crossing is not None
    if crossing is not None:
        out["crossing_is_outside_headline_bracket"] = bool(
            crossing < out["headline_bracket_bp"][0]
            or crossing > out["headline_bracket_bp"][1]
        )
    return out


def main(repo_root: Path, *, note_ids: list[str], spreads_bp: list[float],
         n_paths: int | None = None) -> list[dict]:
    cfg = yaml.safe_load((repo_root / "config/params.yaml").read_text())
    notes = {n["id"]: n for n in
             yaml.safe_load((repo_root / "config/notes.yaml").read_text())["notes"]}
    surfaces = json.loads((repo_root / "data/processed/surfaces.json").read_text())

    out = []
    for note_id in note_ids:
        row = extended_curve(
            notes[note_id], cfg, surfaces, repo_root, spreads_bp=spreads_bp,
            n_paths=n_paths, seed=cfg["monte_carlo"]["seed"],
        )
        out.append(row)
        print(f"  {note_id}  disclosed {row['disclosed_estimated_value']:.2f}")
        for point in row["curve"]:
            marker = "  <- disclosed value sits here" if (
                row["crossing_bp"] is not None
                and abs(point["spread_bp"] - row["crossing_bp"]) < 60
            ) else ""
            print(f"      {point['spread_bp']:>8.0f} bp   {point['value']:8.2f}{marker}")
        if row["crossing_bp"] is None:
            print("      no crossing inside the extended grid either")
        else:
            print(f"      crossing at {row['crossing_bp']:.0f} bp "
                  f"(headline bracket starts at {row['headline_bracket_bp'][0]:.0f} bp)")

    target = repo_root / "data/processed/extended_bracket.json"
    target.write_text(json.dumps({"notes": out}, indent=1, default=str))
    print(f"\nwrote {target.relative_to(repo_root)}")
    return out


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--notes", nargs="+", default=["N4"])
    parser.add_argument(
        "--spreads-bp", nargs="+", type=float,
        default=[-900, -800, -700, -600, -500, -400, -350, -300, -200, -100, 0],
        help="the extended grid, in basis points",
    )
    parser.add_argument("--paths", type=int, default=None)
    args = parser.parse_args()
    main(args.root, note_ids=args.notes, spreads_bp=args.spreads_bp, n_paths=args.paths)
