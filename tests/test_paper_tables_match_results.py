"""Do the manuscript's tables still say what the saved results say?

A number typed into a paper has no way back to the run that produced it. The one
figure in this project that was ever written by hand was wrong, which is why the
report is rendered from a template that refuses to resolve a placeholder it
cannot find. The paper's tables are generated for the same reason.

Generation alone does not protect anything, though: the generator reads a field
name, and a field that gets renamed upstream either raises or, worse, resolves
to something plausible. These tests regenerate the tables into a temporary
directory and check the cells against the saved results independently, so a
silent change in either direction fails here rather than in print.

What is checked is the part a reader would act on: that every model value,
standard error and disclosed value in the results table is the one in
analysis.json, that the gap column carries the sign the finding depends on, and
that the note marking the excluded note is actually present on that note and
absent from the others.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.results import barrier_coverage, figures, load_results


def load_generator():
    """Import the generator by path; scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location(
        "make_paper_tables", ROOT / "scripts/make_paper_tables.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """Every table, rendered once into a throwaway tree."""
    module = load_generator()
    results, meta = load_results(ROOT)
    figs = figures(results, meta, ROOT)
    return {
        "results": module.results_table(results, figs, ROOT),
        "terms": module.note_terms(ROOT, results, figs),
        "spreads": module.spreads(results, figs),
        "decomposition": module.decomposition(results),
        "inputs": module.candidate_inputs(ROOT, results, figs),
        "validation": module.validation(ROOT, figs),
        "census": module.census(ROOT),
    }


@pytest.fixture(scope="module")
def saved():
    results, meta = load_results(ROOT)
    config = {n["id"]: n for n in
              yaml.safe_load((ROOT / "config/notes.yaml").read_text())["notes"]}
    return results, config


def test_every_model_value_and_stderr_appears_in_the_results_table(generated, saved):
    results, _ = saved
    body = generated["results"]
    for note_id, record in results.items():
        assert f"{record['base']['value']:,.2f}" in body, f"{note_id} model value"
        assert f"{record['base']['stderr']:.2f}" in body, f"{note_id} standard error"


def test_disclosed_values_come_from_the_filings_not_the_model(generated, saved):
    _, config = saved
    body = generated["results"]
    for note_id, note in config.items():
        assert f"{note['disclosed_estimated_value']:,.2f}" in body, f"{note_id} disclosed"


def test_the_gap_column_is_negative_for_every_note(generated, saved):
    """The finding is directional. If a gap ever turns positive the table has to
    show it, so the sign is asserted against the arithmetic rather than assumed.
    """
    results, config = saved
    body = generated["results"]
    for note_id, record in results.items():
        gap = record["base"]["value"] - config[note_id]["disclosed_estimated_value"]
        assert gap < 0, f"{note_id} gap is no longer negative; the text must change"
        assert f"$-${abs(gap):,.2f}" in body, f"{note_id} gap cell"


def test_the_excluded_note_is_marked_and_the_others_are_not(generated):
    coverage = barrier_coverage(ROOT)
    excluded = [n for n, v in coverage.items() if not v.get("fully_covered", True)]
    assert excluded, "the coverage rule excludes nothing; the marker is now unused"
    for table in ("results", "terms"):
        body = generated[table]
        for note_id in excluded:
            assert f"{note_id}\\textsuperscript{{a}}" in body, f"{note_id} unmarked in {table}"
        for note_id in coverage:
            if note_id not in excluded:
                assert f"{note_id}\\textsuperscript{{a}}" not in body, \
                    f"{note_id} marked in {table} but its legs are covered"


def test_the_census_rows_partition_the_filing_population(generated):
    """The table claims to partition; a partition that does not add up is a
    different claim about the sample than the one the text will make.
    """
    import json
    records = json.loads((ROOT / "data/processed/edgar_screen.json").read_text())
    assert f"{len(records):,}" in generated["census"]
    stages = {r["screen_stage"] for r in records}
    emitted = generated["census"]
    for stage in stages:
        count = sum(1 for r in records if r["screen_stage"] == stage)
        assert f"{count:,}" in emitted, f"stage {stage} count {count} missing"


def test_no_table_leaves_a_unit_glued_to_a_sentinel(generated):
    """"no solution bp" reads as a quantity. It reached the report once."""
    for name, body in generated.items():
        assert "no solution bp" not in body, name
        assert "n/a bp" not in body, name


def test_every_table_carries_a_label_and_a_note(generated):
    for name, body in generated.items():
        assert "\\label{tab:" in body, f"{name} has no label to reference"
        assert "\\notetext{" in body, f"{name} has no note block"
        assert body.count("\\toprule") == 1 and body.count("\\bottomrule") == 1, name
