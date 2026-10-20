"""The claim audit must fail on a value moved to the wrong sentence.

scripts/audit_paper_numbers.py cannot catch a figure that is valid somewhere
being used somewhere else; that is what the claim audit is for. This pins the
distinction with the case that motivated it: the corporate spread benchmark is
69 basis points, and 70 is a coupon barrier in the same sample, so swapping one
for the other is invisible to a membership check.

An earlier version of the claim audit also missed it, because 69 appears twice
in the results section and the check looked for the value anywhere in the file.
These tests would fail if either regression returned.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TARGET = ROOT / "paper/sections/06_results.tex"


def run_audit():
    """Import fresh each time: the module computes its rows at import."""
    spec = importlib.util.spec_from_file_location(
        "audit_paper_claims", ROOT / "scripts/audit_paper_claims.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.failures()


def test_every_claim_is_supported_where_it_is_made():
    failures = run_audit()
    assert failures == [], "\n".join(f"{c} ({w})" for c, w, _, _, _ in failures)


def test_a_benchmark_moved_to_a_wrong_value_is_caught():
    original = TARGET.read_text()
    anchor = "69 basis points, the option-adjusted spread on A-rated US"
    assert anchor in original, "anchor moved; update this test"
    try:
        TARGET.write_text(original.replace(anchor, anchor.replace("69", "70"), 1))
        failures = run_audit()
        assert any("benchmark" in c for c, *_ in failures), (
            "a wrong benchmark passed the claim audit")
    finally:
        TARGET.write_text(original)


def test_the_file_is_restored():
    assert "69 basis points, the option-adjusted spread" in TARGET.read_text()
