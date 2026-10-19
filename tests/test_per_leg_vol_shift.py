"""Does a per-underlying volatility shift move only the leg it names?

`build_inputs` accepts `bucket_shift` either as one (t_low, t_high, delta) for
the whole basket or as a mapping keyed by ticker. The mapping is what prices the
ETF-proxy bias in src/analysis/proxy_risk.py: DIA's implied vols sit about half
a point above DJX's, and correcting the Dow leg of N2 has to leave the Nasdaq
and Russell legs exactly where they were. If a shift leaked across legs, that
correction would quietly become a parallel move on the whole basket and the
number it produces would mean something else entirely.

There is a second reason to pin this down. The surface also takes a parameter
called `tail_vol_shift`, which reads like a parallel shift and is not one: it
belongs to the frozen_shifted extrapolation mode and bites only past the last
listed expiry. An earlier version of proxy_risk.py reached for that name and
produced a correction of exactly zero. These tests fail if the working
mechanism stops working, and the naming is now explicit enough that the two
cannot be confused again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pricer.engine import build_inputs

SURFACES = ROOT / "data/processed/surfaces.json"

pytestmark = pytest.mark.skipif(
    not SURFACES.exists(), reason="run src.vol.calibrate first"
)


@pytest.fixture(scope="module")
def setup():
    cfg = yaml.safe_load((ROOT / "config/params.yaml").read_text())
    notes = {n["id"]: n for n in
             yaml.safe_load((ROOT / "config/notes.yaml").read_text())["notes"]}
    surfaces = json.loads(SURFACES.read_text())
    return cfg, notes, surfaces


def _atm_vols(inputs, maturity: float) -> dict[str, float]:
    """At-the-money implied vol per leg, read off each surface at one maturity."""
    return {
        ticker: float(surface.implied_vol(np.array([0.0]), maturity)[0])
        for ticker, surface in zip(inputs.terms.tickers, inputs.surfaces)
    }


def test_named_leg_moves_and_the_others_do_not(setup):
    cfg, notes, surfaces = setup
    # N2 is the note with the proxied leg, so it is the one that matters here.
    record = notes["N2"]
    shift = 0.01  # one vol point, in the decimal units the surface uses

    base = build_inputs(record, cfg, surfaces, ROOT)
    bumped = build_inputs(record, cfg, surfaces, ROOT, bucket_shift={"DJX": (0.0, 1e9, shift)})

    maturity = float(base.terms.observation_times[-1]) * 0.5
    before = _atm_vols(base, maturity)
    after = _atm_vols(bumped, maturity)

    assert "DJX" in before, f"expected a Dow leg on N2, got {sorted(before)}"
    assert after["DJX"] == pytest.approx(before["DJX"] + shift, abs=2e-4), (
        f"the Dow leg should move by {shift:.4f}: "
        f"{before['DJX']:.6f} -> {after['DJX']:.6f}"
    )

    for ticker in before:
        if ticker == "DJX":
            continue
        assert after[ticker] == pytest.approx(before[ticker], abs=1e-12), (
            f"{ticker} should not have moved: "
            f"{before[ticker]:.9f} -> {after[ticker]:.9f}"
        )


def test_a_scalar_shift_still_moves_every_leg(setup):
    """The mapping form must not have broken the whole-basket form."""
    cfg, notes, surfaces = setup
    record = notes["N2"]
    shift = 0.01

    base = build_inputs(record, cfg, surfaces, ROOT)
    bumped = build_inputs(record, cfg, surfaces, ROOT, bucket_shift=(0.0, 1e9, shift))

    maturity = float(base.terms.observation_times[-1]) * 0.5
    before = _atm_vols(base, maturity)
    after = _atm_vols(bumped, maturity)

    for ticker in before:
        assert after[ticker] == pytest.approx(before[ticker] + shift, abs=2e-4), (
            f"{ticker} should have moved by {shift:.4f}: "
            f"{before[ticker]:.6f} -> {after[ticker]:.6f}"
        )


def test_an_unnamed_ticker_in_the_mapping_is_harmless(setup):
    """A mapping naming a ticker the note does not reference changes nothing.

    proxy_risk.py builds its shift from the measured bias for every proxied
    underlying and hands the same mapping to each affected note, so a note
    without that leg has to come back untouched rather than raising.
    """
    cfg, notes, surfaces = setup
    record = notes["N6"]  # RUT, SPX, SMH: no Dow leg

    base = build_inputs(record, cfg, surfaces, ROOT)
    bumped = build_inputs(record, cfg, surfaces, ROOT, bucket_shift={"DJX": (0.0, 1e9, 0.05)})

    maturity = float(base.terms.observation_times[-1]) * 0.5
    before = _atm_vols(base, maturity)
    after = _atm_vols(bumped, maturity)

    assert before.keys() == after.keys()
    for ticker in before:
        assert after[ticker] == pytest.approx(before[ticker], abs=1e-12)
