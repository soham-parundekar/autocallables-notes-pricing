"""Does the headline sample exclude exactly the notes the coverage test fails?

The report quotes ranges over a subset of the sample. A note is left out when
any of its legs has no quoted strike at the note's downside trigger on any
fitted slice past one year, because the barrier is then priced off an
extrapolation and averaging it into a range would present it as a measurement
on the same footing as the rest.

That is a defensible rule and an easy one to abuse, so it is pinned here. The
risk is not that the rule is wrong; it is that it quietly becomes something
else — a note dropped because its number was inconvenient, or a threshold moved
until the range looked better. These tests assert that the partition follows
from the coverage count alone, that it never empties the sample, and that the
excluded note's own figures are still published so the exclusion cannot hide
anything.

The direction is worth recording: on this sample the rule removes the *widest*
gap, so the headline range it produces is narrower than the one over all six.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.results import barrier_coverage, figures, load_results

pytestmark = pytest.mark.skipif(
    not (ROOT / "data/processed/surfaces.json").exists(),
    reason="run src.vol.calibrate first",
)


@pytest.fixture(scope="module")
def built():
    notes, meta = load_results(ROOT)
    if not notes:
        pytest.skip("no priced notes in this checkout")
    return notes, figures(notes, meta, ROOT), barrier_coverage(ROOT)


def test_the_partition_follows_from_the_coverage_count(built):
    notes, values, coverage = built
    headline = set(values["headline_ids"].split(", "))
    excluded = set(
        v for v in values["excluded_ids"].split(", ") if v and v != "none"
    )

    assert headline | excluded == set(notes), "every priced note must land on one side"
    assert not (headline & excluded), "a note cannot be in both"

    for note_id in notes:
        fails = not coverage.get(note_id, {}).get("fully_covered", True)
        assert (note_id in excluded) == fails, (
            f"{note_id} is {'excluded' if note_id in excluded else 'in the headline'} "
            f"but its coverage says {'it fails' if fails else 'it passes'}. The "
            f"partition must follow from the coverage count, not from anything else."
        )


def test_an_excluded_note_still_publishes_its_own_numbers(built):
    """Holding a note out of the ranges must not remove it from the report."""
    _, values, _ = built
    excluded = [v for v in values["excluded_ids"].split(", ") if v and v != "none"]
    if not excluded:
        pytest.skip("every note passes the coverage test in this checkout")

    for note_id in excluded:
        for field in ("excluded_gap", "excluded_model", "excluded_stderr"):
            key = f"{field}_{note_id}"
            assert key in values and values[key], (
                f"{note_id} is held out of the ranges but {key} is not published, "
                f"so its number would vanish from the report"
            )


def test_the_rule_never_empties_the_sample(built):
    _, values, _ = built
    assert int(values["headline_count"]) >= 1
    assert int(values["headline_count"]) + int(values["excluded_count"]) == int(
        values["note_count"]
    )


def test_the_headline_range_spans_only_headline_notes(built):
    """The quoted gap range must be attributed to notes inside the headline set."""
    _, values, _ = built
    headline = set(values["headline_ids"].split(", "))
    assert values["gap_smallest_note"] in headline
    assert values["gap_largest_note"] in headline


def test_excluding_a_note_did_not_flatter_the_result(built):
    """The exclusion must not be what makes the headline finding look better.

    On this sample the held-out note is further from its disclosed value than
    any note left in, so dropping it narrows the range rather than widening it.
    If that ever reverses, the rule is removing an inconvenient result and the
    report needs to say so explicitly rather than let a range speak for it.
    """
    notes, values, _ = built
    excluded = [v for v in values["excluded_ids"].split(", ") if v and v != "none"]
    if not excluded:
        pytest.skip("every note passes the coverage test in this checkout")

    widest_headline = float(values["gap_largest"].replace(",", ""))
    for note_id in excluded:
        base = notes[note_id]["base"]
        gap = abs(base["value"] - base["disclosed_estimated_value"])
        assert gap >= widest_headline, (
            f"{note_id} was excluded and its gap ({gap:.2f}) is smaller than the "
            f"widest gap left in ({widest_headline:.2f}), so the exclusion widened "
            f"the headline range. Say that in the report rather than leaving it to "
            f"the numbers."
        )
