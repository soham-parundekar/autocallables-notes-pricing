"""The number audit has to fail when it should, not merely pass when asked.

An audit that passes because it is broken is worse than no audit, because it
converts an unchecked manuscript into one that looks checked. The first version
of scripts/audit_paper_numbers.py had that defect: a fallback for
comma-formatted integers called int() on every literal, so a planted 11.47
matched the 11 before its decimal point and the whole manuscript came back
clean.

These tests plant a value with no source in the project and assert the audit
notices, then assert the real manuscript passes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SECTIONS = ROOT / "paper/sections"


def load_audit():
    spec = importlib.util.spec_from_file_location(
        "audit_paper_numbers", ROOT / "scripts/audit_paper_numbers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit():
    return load_audit()


def test_the_manuscript_passes(audit):
    findings = audit.audit(ROOT)
    assert findings == [], "\n".join(findings)


def test_a_planted_value_with_no_source_is_caught(audit):
    """The decimal has to be one no stored value rounds to."""
    target = SECTIONS / "06_results.tex"
    original = target.read_text()
    assert "\\$11.41" in original, "anchor moved; update this test"
    try:
        target.write_text(original.replace("\\$11.41", "\\$11.47", 1))
        findings = audit.audit(ROOT)
        assert any("11.47" in f for f in findings), (
            "the audit passed a value with no source in the project")
    finally:
        target.write_text(original)


def test_restoring_the_file_restores_a_clean_audit(audit):
    assert audit.audit(ROOT) == []
