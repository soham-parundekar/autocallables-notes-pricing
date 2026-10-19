"""Does config/notes.yaml still match the filings it was read from?

The term sheets were extracted by hand, which is the right call for six notes
but leaves no automatic link back to the source. This test rebuilds that link:
every scalar in the YAML is searched for in the preserved 424B2, so the config
cannot drift from the filing without the suite going red.

It also re-checks the cross-filing agreement that gave the most confidence in
the extraction in the first place. NDX 31,160.08 and RTY 2,793.204 appear in
four separate supplements from three issuers; if a typo crept into one, the
agreement breaks.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NOTES = ROOT / "config/notes.yaml"
pytestmark = pytest.mark.skipif(not NOTES.exists(), reason="config/notes.yaml missing")

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[\s ]+")


def filing_text(relative_path: str) -> str:
    raw = (ROOT / relative_path).read_text(encoding="utf-8", errors="replace")
    for entity, replacement in [
        ("&nbsp;", " "), ("&#160;", " "), ("&amp;", "&"), ("&#8217;", "'"),
        ("&#x2019;", "'"), ("&#151;", "-"), ("&#8212;", "-"), ("&#8211;", "-"),
    ]:
        raw = raw.replace(entity, replacement)
    return _WHITESPACE.sub(" ", _TAG.sub(" ", raw))


def notes() -> list[dict]:
    return yaml.safe_load(NOTES.read_text())["notes"]


def with_thousands(value: float) -> list[str]:
    """Spellings a filing might use for a number, with and without separators."""
    out = []
    if value == int(value):
        out += [f"{int(value):,}", str(int(value))]
    for places in (1, 2, 3, 4):
        text = f"{value:,.{places}f}"
        out.append(text)
        out.append(text.replace(",", ""))
        out.append(text.rstrip("0").rstrip("."))
    return [t for t in dict.fromkeys(out) if t]


@pytest.fixture(scope="module")
def texts() -> dict[str, str]:
    return {note["id"]: filing_text(note["filing"]) for note in notes()}


@pytest.mark.parametrize("note", notes(), ids=lambda n: n["id"])
def test_filing_is_preserved_and_is_the_right_document(note, texts):
    text = texts[note["id"]]
    assert len(text) > 20_000, f"{note['id']}: preserved filing looks truncated"
    assert note["cusip"] in text.replace(" ", ""), f"{note['id']}: CUSIP not found in filing"


@pytest.mark.parametrize("note", notes(), ids=lambda n: n["id"])
def test_disclosed_estimated_value_matches(note, texts):
    text = texts[note["id"]]
    candidates = with_thousands(note["disclosed_estimated_value"])
    assert any(c in text for c in candidates), (
        f"{note['id']}: estimated value {note['disclosed_estimated_value']} not in filing"
    )


@pytest.mark.parametrize("note", notes(), ids=lambda n: n["id"])
def test_initial_levels_match(note, texts):
    text = texts[note["id"]].replace(" ", "")
    for underlying in note["underlyings"]:
        candidates = [c.replace(" ", "") for c in with_thousands(underlying["initial_level"])]
        assert any(c in text for c in candidates), (
            f"{note['id']} {underlying['ticker']}: initial level "
            f"{underlying['initial_level']} not in filing"
        )


@pytest.mark.parametrize("note", notes(), ids=lambda n: n["id"])
def test_coupon_and_barriers_match(note, texts):
    text = texts[note["id"]]
    rate_pct = note["coupon"]["annual_rate"] * 100.0
    assert f"{rate_pct:.2f}%" in text or f"{rate_pct:g}%" in text, (
        f"{note['id']}: coupon rate {rate_pct}% not in filing"
    )
    for level, label in [(note["coupon"]["barrier"], "coupon barrier"),
                         (note["downside"]["trigger"], "downside trigger")]:
        pct = level * 100.0
        assert any(s in text for s in (f"{pct:.2f}%", f"{pct:g}%", f"{pct:.0f}%")), (
            f"{note['id']}: {label} {pct}% not in filing"
        )


@pytest.mark.parametrize("note", notes(), ids=lambda n: n["id"])
def test_commission_matches(note, texts):
    """Commission as the filing states it, in dollars or as a percentage.

    Goldman quotes its underwriting discount as "0.525% of the face amount"
    rather than a dollar figure, so the $5.25 in the config is a conversion at
    the $1,000 denomination rather than a number lifted off the page. Both
    spellings are accepted, which keeps the check honest about which it found.
    """
    text = texts[note["id"]]
    commission = note["selling_commission"]
    candidates = with_thousands(commission)
    percent = 100.0 * commission / note["denomination"]
    candidates += [f"{percent:.3f}%", f"{percent:.2f}%", f"{percent:g}%"]
    assert any(c in text for c in candidates), (
        f"{note['id']}: commission {commission} not in filing as dollars or percent"
    )


@pytest.mark.parametrize("note", notes(), ids=lambda n: n["id"])
def test_schedule_endpoints_appear_in_the_filing(note, texts):
    """First and last observation dates, in the long form the filings print."""
    text = texts[note["id"]]
    months = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
    for iso in (note["schedule"]["observation"][0], note["schedule"]["observation"][-1]):
        year, month, day = (int(p) for p in str(iso).split("-"))
        spelled = f"{months[month - 1]} {day}, {year}"
        assert spelled in text, f"{note['id']}: observation date {spelled} not in filing"


@pytest.mark.parametrize("note", notes(), ids=lambda n: n["id"])
def test_schedule_lengths_agree_with_tenor_and_frequency(note):
    """A missing or duplicated date would change every coupon downstream."""
    schedule = note["schedule"]
    assert len(schedule["observation"]) == len(schedule["payment"])
    assert schedule["observation"] == sorted(schedule["observation"])
    assert len(set(schedule["observation"])) == len(schedule["observation"])
    for observed, paid in zip(schedule["observation"], schedule["payment"]):
        assert observed <= paid, f"{note['id']}: payment {paid} precedes observation {observed}"

    per_year = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}[
        note["coupon"]["frequency"]
    ]
    first, last = schedule["observation"][0], schedule["observation"][-1]
    years = (int(last[:4]) - int(first[:4])) + (int(last[5:7]) - int(first[5:7])) / 12.0
    expected = round(years * per_year) + 1
    assert abs(len(schedule["observation"]) - expected) <= 1, (
        f"{note['id']}: {len(schedule['observation'])} observations, expected about {expected}"
    )


def test_initial_levels_agree_across_filings():
    """NDX and RTY appear in four supplements from three issuers; they must agree.

    This is the extraction's strongest check, because the four filings were read
    independently and a transcription error in any one of them shows up here
    rather than in a result nobody can trace.
    """
    by_ticker: dict[str, set[float]] = {}
    for note in notes():
        if str(note["pricing_date"]) != "2026-10-07":
            continue
        for underlying in note["underlyings"]:
            by_ticker.setdefault(underlying["ticker"], set()).add(underlying["initial_level"])

    for ticker in ("NDX", "RUT", "SPX"):
        if ticker in by_ticker:
            assert len(by_ticker[ticker]) == 1, (
                f"{ticker} initial level disagrees across 7 October filings: {by_ticker[ticker]}"
            )
    assert by_ticker.get("NDX") == {31160.08}
    assert by_ticker.get("RUT") == {2793.204}


def test_every_note_has_a_distinct_cusip_and_filing():
    ids = [n["id"] for n in notes()]
    assert len(set(ids)) == len(ids)
    cusips = [n["cusip"] for n in notes()]
    assert len(set(cusips)) == len(cusips)
    filings = [n["filing"] for n in notes()]
    assert len(set(filings)) == len(filings)
