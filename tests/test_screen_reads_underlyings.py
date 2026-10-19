"""Does the screener read a filing's underlyings, and refuse to confuse them?

Two failures in this project came from this one function, and both were silent.

The first was reading the Nasdaq-100 Technology Sector Index as the Nasdaq-100.
NDXT has no usable listed option market, so a note linked to it is not
priceable, and matching its name loosely pulled those notes into the priceable
pool where they would have been priced against the Nasdaq-100's surface. The
name "Nasdaq-100 Technology Sector Index" contains "Nasdaq-100", so the defence
is ordering plus cutting each match out of the text, and ordering is the kind of
thing a later edit breaks without noticing.

The second was markup. Several issuers put a registered mark on every index name
in the title, and "S&P 500 &reg; Index" does not match a pattern written for
"S&P 500 Index". That one bug hid 25 priceable notes out of 129, which would
have gone into the write-up as a claim about how little of this market can be
priced. Entity decoding and trademark stripping are therefore load-bearing, not
housekeeping.

The fixtures here are the shapes the real filings use, written out rather than
loaded, so the test says what it is testing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.edgar_notes import INDEX_CODES, identify_underlyings, to_text


def test_the_technology_sector_index_is_not_the_nasdaq_100():
    """The trap. NDXT must come back as NDXT, and must not bring NDX with it."""
    text = (
        "Callable Contingent Coupon Equity Linked Securities Linked to the Worst "
        "Performing of the Nasdaq-100 Technology Sector Index, the Russell 2000 "
        "Index and the S&P 500 Index due October 11, 2029"
    )
    codes, chains = identify_underlyings(text)
    assert "NDXT" in codes, f"expected NDXT in {codes}"
    assert "NDX" not in codes, (
        f"the Technology Sector Index was read as the Nasdaq-100: {codes}"
    )
    # And it must be marked unpriceable, which is the point of identifying it.
    assert chains[codes.index("NDXT")] is None
    assert INDEX_CODES["NDXT"] is None


def test_the_plain_nasdaq_100_is_still_recognised():
    """The ordering must not have gone so far that NDX stops matching."""
    text = (
        "Linked to the Worst Performing of the Dow Jones Industrial Average, the "
        "Nasdaq-100 Index and the Russell 2000 Index due October 10, 2031"
    )
    codes, chains = identify_underlyings(text)
    assert set(codes) == {"INDU", "NDX", "RTY"}, codes
    assert all(chains), f"all three should be priceable: {list(zip(codes, chains))}"


def test_both_indices_are_found_when_a_filing_names_both():
    text = (
        "Linked to the Worst Performing of the Nasdaq-100 Index, the Nasdaq-100 "
        "Technology Sector Index and the S&P 500 Index"
    )
    codes, _ = identify_underlyings(text)
    assert "NDX" in codes and "NDXT" in codes, codes


@pytest.mark.parametrize(
    "markup",
    [
        "S&amp;P 500 &reg; Index",
        "S&P 500 ® Index",
        "S&amp;P 500&#174; Index",
        "S&P 500 Index",
    ],
)
def test_trademark_markup_does_not_hide_an_index_name(markup):
    """Every spelling of the same index in the sample has to read the same.

    This is the bug that hid 25 of 129 priceable notes: the mark sits between
    "500" and "Index", so a pattern written for the clean name never fires.
    """
    html = (
        f"<p>Linked to the Worst Performing of the Nasdaq-100 Index, the "
        f"Russell 2000 Index and the {markup} due 2029</p>"
    )
    codes, chains = identify_underlyings(to_text(html))
    assert "SPX" in codes, f"{markup!r} was not read as the S&P 500: {codes}"
    assert set(codes) == {"NDX", "RTY", "SPX"}, codes
    assert all(chains)


def test_a_bloomberg_ticker_declaration_still_wins():
    """The name table is a fallback; an explicit ticker block takes precedence."""
    text = (
        "Underlyings: the Nasdaq-100 Index (Bloomberg ticker: NDX), the Russell "
        "2000 Index (Bloomberg ticker: RTY) and the S&P 500 Index "
        "(Bloomberg ticker: SPX)"
    )
    codes, chains = identify_underlyings(text)
    assert codes[:3] == ["NDX", "RTY", "SPX"], codes
    assert all(chains)


def test_a_single_name_basket_yields_no_index_codes():
    """Worst-of notes on individual companies must not produce index codes.

    These are a real part of the market and outside this project's scope. What
    matters is that they come back empty rather than matching something, so the
    screen counts them as single-name notes instead of as filings it failed to
    read.
    """
    text = (
        "Auto Callable Contingent Interest Notes Linked to the Least Performing "
        "of the Common Stock of The Cooper Companies, Inc., the Class A Common "
        "Stock of Carvana Co. and the Common Stock of Broadcom Inc."
    )
    codes, _ = identify_underlyings(text)
    assert codes == [], f"a single-name basket produced index codes: {codes}"


def test_entities_decode_without_reintroducing_tags():
    """to_text decodes entities after stripping tags, not before.

    Decoding first would let an entity inside an attribute turn into markup that
    the tag stripper has already run past.
    """
    html = '<p title="&lt;script&gt;">Nasdaq-100 Index &amp; more</p>'
    text = to_text(html)
    assert "<script>" not in text
    assert "Nasdaq-100 Index & more" in text
