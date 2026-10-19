"""What does pricing the Dow leg off DIA instead of DJX cost?

One underlying in the sample has no usable option market of its own. DJX leaves
two tradable expiries after cleaning, the furthest at 0.69 years, against a note
that matures in 2031, so the Dow surface is fitted to DIA and N2 is priced off
it. src/vol/proxy_check.py measures what that substitution does to the implied
vols: DIA's at-the-money vols sit about half a point above DJX's, and the same
gap appears in the two control pairs where the project never needs a proxy
(QQQ above NDX, IWM above RUT, both by a similar amount).

A consistent gap in a known direction is a bias rather than noise, so it can be
corrected and priced. This module reprices the affected note with only the
proxied leg's surface shifted down by the measured gap, leaving the other
underlyings untouched, and reports the difference.

The sign matters for how the result is read. The investor in these notes is
short a down-and-in put, so an overstated volatility makes the put too
expensive and the note too cheap. Correcting the bias should therefore raise
the model value and narrow the gap to the disclosed value, which makes this one
of the few corrections in the project that runs in that direction. Whether it
is large enough to matter is the question the numbers answer.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from ..pricer.engine import build_controls, build_inputs, run_simulation
from ..pricer.payoff import price


def measured_bias(repo_root: Path) -> dict[str, float]:
    """Per proxied ticker, the ETF's excess at-the-money vol in decimal units.

    Read from the proxy check's own summary so this cannot drift from the
    measurement. The sign is returned as the shift to apply: the ETF's vols sit
    above the index's, so the correction is negative.
    """
    path = repo_root / "data/processed/proxy_summary.csv"
    if not path.exists():
        raise SystemExit(
            "data/processed/proxy_summary.csv is missing; run "
            "python3 -m src.vol.proxy_check first"
        )
    out: dict[str, float] = {}
    for row in csv.DictReader(path.read_text().splitlines()):
        if not row.get("shared_expiries") or int(row["shared_expiries"]) == 0:
            continue
        # mean_difference is (etf - index) in vol points; the shift that removes
        # it from an ETF-derived surface is its negative, in decimal vol.
        out[row["index"]] = -float(row["mean_difference_vol_pts"]) / 100.0
    return out


def proxied_tickers(cfg: dict) -> set[str]:
    """Underlyings whose surface is fitted to a different instrument's options."""
    mapping = cfg["underlyings"] if "underlyings" in cfg else cfg.get("underlying_chains", {})
    return {
        ticker for ticker, spec in mapping.items()
        if isinstance(spec, dict) and spec.get("proxy_for")
    }


def assess(
    note_record: dict, cfg: dict, surface_records: dict, repo_root: Path,
    *, shifts: dict[str, float], n_paths: int, seed: int,
) -> dict:
    """Price the note as calibrated, then with the proxied leg corrected."""
    use_controls = bool(cfg["monte_carlo"].get("control_variate"))
    tickers = [u["ticker"] for u in note_record["underlyings"]]
    applied = {t: shifts[t] for t in tickers if t in shifts}

    def value_with(shift: dict[str, float]) -> dict:
        # One bucket spanning every maturity, applied only to the proxied legs,
        # which is a parallel shift of those surfaces and nothing else.
        buckets = {t: (0.0, 1e9, delta) for t, delta in shift.items()}
        inputs = build_inputs(note_record, cfg, surface_records, repo_root,
                              bucket_shift=buckets or None)
        sim = run_simulation(inputs, cfg, n_paths=n_paths, seed=seed)
        controls = build_controls(inputs, sim) if use_controls else None
        return price(inputs.terms, sim, inputs.curve, controls=controls)

    base = value_with({})
    corrected = value_with(applied)
    disclosed = float(note_record["disclosed_estimated_value"])

    return {
        "note_id": note_record["id"],
        "proxied_legs": applied,
        "shift_vol_points": {t: round(v * 100, 3) for t, v in applied.items()},
        "value_as_calibrated": base["value"],
        "value_proxy_corrected": corrected["value"],
        "difference": corrected["value"] - base["value"],
        "stderr": base["stderr"],
        "disclosed_estimated_value": disclosed,
        "gap_as_calibrated": base["value"] - disclosed,
        "gap_proxy_corrected": corrected["value"] - disclosed,
    }


def main(repo_root: Path, *, note_ids: list[str] | None = None,
         n_paths: int | None = None) -> list[dict]:
    cfg = yaml.safe_load((repo_root / "config/params.yaml").read_text())
    notes = {n["id"]: n for n in
             yaml.safe_load((repo_root / "config/notes.yaml").read_text())["notes"]}
    surfaces = json.loads((repo_root / "data/processed/surfaces.json").read_text())

    shifts = measured_bias(repo_root)
    proxied = proxied_tickers(cfg)
    relevant = {t: v for t, v in shifts.items() if t in proxied}
    if not relevant:
        print("no proxied underlyings in the configuration; nothing to assess")
        return []
    print(f"  correcting {', '.join(f'{t} by {v * 100:+.3f} vol pts' for t, v in relevant.items())}")

    affected = note_ids or [
        note_id for note_id, record in sorted(notes.items())
        if any(u["ticker"] in relevant for u in record["underlyings"])
    ]
    if not affected:
        print("no note in the sample references a proxied underlying")
        return []

    paths = n_paths or cfg["monte_carlo"]["paths"]
    out = []
    for note_id in affected:
        row = assess(notes[note_id], cfg, surfaces, repo_root, shifts=relevant,
                     n_paths=paths, seed=cfg["monte_carlo"]["seed"])
        out.append(row)
        print(f"  {note_id}  as calibrated {row['value_as_calibrated']:8.2f}  "
              f"proxy corrected {row['value_proxy_corrected']:8.2f}  "
              f"difference {row['difference']:+6.2f}  "
              f"(se {row['stderr']:.2f})", flush=True)

    target = repo_root / "data/processed/proxy_risk.json"
    target.write_text(json.dumps({"notes": out}, indent=1, default=str))
    print(f"wrote {target.relative_to(repo_root)}")
    return out


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--notes", nargs="*", default=None)
    parser.add_argument("--paths", type=int, default=None)
    args = parser.parse_args()
    main(args.root, note_ids=args.notes, n_paths=args.paths)
