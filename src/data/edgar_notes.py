"""Find and screen 424B2 pricing supplements on EDGAR.

This is how the sample was selected, kept as a script so the population counts
in the report can be reproduced rather than taken on trust. It does not parse
term sheets: with six notes, the fields that matter are read by hand into
config/notes.yaml and checked against the filings by
tests/test_term_sheets_match_filings.py, which is both more accurate and easier
to audit than a general-purpose parser at this scale.

What the screen has to get right, learned by getting it wrong first:

Search under the financing subsidiary, not the bank. These programmes issue
through JPMorgan Chase Financial Company LLC, GS Finance Corp., Morgan Stanley
Finance LLC and so on. Searching the parent's CIK finds almost nothing.

Separate preliminary from final supplements. Only the final one carries the
initial levels and the estimated value as a definite number; the preliminary one
quotes a range (Goldman) or an approximation with a tolerance (Morgan Stanley),
and the pricing date it states is the date pricing is *expected*. Screening on a
stated pricing date without checking for preliminary wording silently mixes
notes that have not priced yet into the sample.

Read the estimated value at the issuer's own denomination. Morgan Stanley issues
some of these at $10 per security rather than $1,000, so a pattern requiring
three digits before the decimal point misses them entirely.

Identify underlyings from the Bloomberg ticker, not the index name. "Nasdaq-100
Technology Sector Index" contains "Nasdaq-100" but is NDXT, a different index
with no usable option market. Matching on names put NDXT-linked notes into the
priceable pool, where they would have been priced against the wrong surface.
NDXT turned out to be the single largest reason a note in this population cannot
be priced from free data.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from html import unescape
from dataclasses import dataclass, field
from pathlib import Path

import requests

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document}"

# Structured-note financing subsidiaries, with the CIK each one files under.
ISSUER_CIKS = {
    "JPMorgan Chase Financial Co. LLC": "0001665650",
    "GS Finance Corp.": "0001419828",
    "Morgan Stanley Finance LLC": "0001666268",
    "Citigroup Global Markets Holdings Inc.": "0000200245",
    "BofA Finance LLC": "0001682472",
}

# Bloomberg index codes mapped onto the chain this project calibrates from.
# None means no free listed option market was captured for it, so a note
# referencing it cannot be priced here.
INDEX_CODES = {
    "SPX": "SPX", "NDX": "NDX", "RTY": "RUT", "INDU": "DJX", "DJI": "DJX", "XSP": "XSP",
    "NDXT": None, "SPXEW": None, "SX5E": None, "NKY": None, "UKX": None, "HSI": None,
    "MXEF": None, "DAX": None, "RTYV": None, "RTYG": None, "MID": None, "SPTSX": None,
    # Index tickers that carry digits, kept explicit so a truncated capture or a
    # new spelling cannot be mistaken for a tradable single-name chain.
    "SX5E": None, "SPTSX60": None, "RTYFPE": None, "NDXE": None, "SPXEW": None,
}

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[\s ]+")
# Characters that land in the middle of an index name and break a match on it:
# the trademark and registered marks, non-breaking spaces, and the typographic
# dashes and quotes. "S&P 500 (R) Index" has to read as "S&P 500 Index" before
# any name pattern sees it. html.unescape handles the entity decoding itself,
# including the named forms (&reg;, &trade;) that an explicit table kept missing.
_MARKUP_NOISE = re.compile(r"[\u00ae\u2122\u00a9\u00a0\u200b]+")
_PUNCTUATION_FOLD = [
    ("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'),
    ("\u2014", "-"), ("\u2013", "-"), ("\u2212", "-"),
]

PRELIMINARY = re.compile(
    r"preliminary pricing supplement|information in this preliminary|subject to completion", re.I
)
WORST_OF = re.compile(
    r"worst[- ]performing|least[- ]performing|lesser[- ]performing|lowest[- ]performing"
    r"|lowest underlier return", re.I
)
CONTINGENT_COUPON = re.compile(
    r"contingent (?:interest|coupon)|contingent income|contingent quarterly|contingent monthly", re.I
)
AUTOMATIC_CALL = re.compile(
    r"automatically (?:be )?(?:called|redeemed)|automatic(?:ally)? call|will be called if"
    r"|autocall|automatic early redemption", re.I
)
ISSUER_CALL = re.compile(
    r"at our (?:option|election)|at the [Ii]ssuer'?s? (?:option|election)|[Ii]ssuer [Cc]allable"
    r"|we may, at our|we, at our election|economically rational for us", re.I
)
# Any dollar amount near the estimated-value language, at any denomination.
ESTIMATED_VALUE = re.compile(
    r"estimated value[^$]{0,260}?\$\s?([0-9][0-9,]*(?:\.[0-9]{1,3})?)", re.I
)
ESTIMATED_VALUE_RANGE = re.compile(
    r"estimated value[^$]{0,260}?\$\s?([0-9][0-9,]*(?:\.[0-9]{1,3})?)\s*(?:to|-|and)\s*"
    r"\$\s?([0-9][0-9,]*(?:\.[0-9]{1,3})?)", re.I
)
# Bloomberg index tickers contain digits: SX5E, SPTSX60, RTYFPE. A letters-only
# capture truncated SX5E to "SX", which then passed the "looks like a ticker"
# test and put EuroStoxx-linked notes in the priceable pool. The ticker must
# start with a letter and may carry digits after it.
BLOOMBERG_TICKER = re.compile(
    r'Bloomberg (?:ticker|symbol)s?\s*(?:is|:)?\s*[:"“]*\s*([A-Z][A-Z0-9]{0,7})', re.I
)
QUOTED_INDEX = re.compile(r'\(\s*(?:the\s+)?["“]([A-Z]{1,8})\s+(?:Index|Fund|ETF)?["”]?\s*\)')

# Fallback for filings that name their underlyings in the title and never print
# a Bloomberg ticker. Citigroup does this on every note in the sample, which is
# 28 filings that the ticker parser alone reads as having no underlyings at all.
#
# Matching index names is what produced the worst screening bug in this project:
# a loose `Nasdaq-?100` matched the Nasdaq-100 Technology Sector Index, which has
# no usable option market, and pulled NDXT-linked notes into the priceable pool
# to be priced against the wrong surface. So the rules here are ordered most
# specific first and each match is cut out of the text before the next pattern
# runs, which makes it impossible for "Nasdaq-100 Index" to match inside
# "Nasdaq-100 Technology Sector Index". The order is load-bearing; the test in
# tests/test_screen_reads_underlyings.py pins it.
# Worst-of notes written on individual companies or sector funds rather than on
# broad indices. They are a real and large part of this market, but they are not
# what this project prices, and they drop out of the screen because no index
# name matches. Recognising them keeps them from being counted as filings the
# screener failed to read, which is a different and much less flattering claim.
SINGLE_NAME_UNDERLYING = re.compile(
    r"(?:common stock|class [ab] common stock|american depositary shares|"
    r"shares of)\s+of\b|\bETF\b", re.I
)

INDEX_NAMES: tuple[tuple[str, str], ...] = (
    (r"Nasdaq-?\s*100\s+Technology\s+Sector\s+Index", "NDXT"),
    (r"S&P\s*500\s+Equal\s+Weight\s+Index", "SPXEW"),
    (r"Nasdaq-?\s*100\s+Index", "NDX"),
    (r"Dow\s+Jones\s+Industrial\s+Average", "INDU"),
    (r"Russell\s*2000(?:\s+Value|\s+Growth)?\s+Index", "RTY"),
    (r"S&P\s*500\s+Index", "SPX"),
    (r"S&P\s*MidCap\s*400\s+Index", "MID"),
    (r"EURO\s*STOXX\s*50", "SX5E"),
    (r"Nikkei\s*225", "NKY"),
    (r"FTSE\s*100", "UKX"),
    (r"Hang\s+Seng", "HSI"),
    (r"DAX\s+Index", "DAX"),
    (r"S&P/TSX\s*60", "SPTSX"),
    (r"MSCI\s+Emerging\s+Markets", "MXEF"),
)
_INDEX_NAME_PATTERNS = tuple(
    (re.compile(pattern, re.I), code) for pattern, code in INDEX_NAMES
)


@dataclass
class Screen:
    """One filing's screening result."""

    issuer: str
    cik: str
    accession: str
    document: str
    filing_date: str
    url: str
    is_preliminary: bool = False
    is_worst_of: bool = False
    has_contingent_coupon: bool = False
    estimated_value: float | None = None
    estimated_value_is_range: bool = False
    call_style: str = "unknown"
    underlying_codes: list[str] = field(default_factory=list)
    underlying_chains: list[str | None] = field(default_factory=list)
    underlyings_are_single_names: bool = False

    @property
    def is_final_worst_of(self) -> bool:
        return (
            not self.is_preliminary
            and self.is_worst_of
            and self.has_contingent_coupon
            and self.estimated_value is not None
            and not self.estimated_value_is_range
        )

    @property
    def is_priceable(self) -> bool:
        return (
            self.is_final_worst_of
            and len(self.underlying_chains) >= 2
            and all(self.underlying_chains)
        )

    def as_record(self) -> dict:
        """Flatten to JSON, with the derived verdicts written out.

        The two verdicts are properties, so a plain vars() drops them and the
        saved screen cannot be read back without re-deriving the rules. Writing
        them down makes the file answer the question it exists to answer, and
        `screen_stage` says where a filing left the funnel so that the counts in
        the report can be reconciled without guessing.
        """
        record = dict(vars(self))
        record["is_final_worst_of"] = self.is_final_worst_of
        record["is_priceable"] = self.is_priceable
        record["screen_stage"] = self._stage()
        return record

    def _stage(self) -> str:
        if self.is_preliminary:
            return "preliminary"
        if not self.is_worst_of:
            return "not_worst_of"
        if not self.has_contingent_coupon:
            return "no_contingent_coupon"
        if self.estimated_value is None:
            return "no_estimated_value"
        if self.estimated_value_is_range:
            return "estimated_value_is_a_range"
        if len(self.underlying_chains) < 2:
            return ("single_name_or_fund_underlyings"
                    if self.underlyings_are_single_names
                    else "underlyings_not_identified")
        if not all(self.underlying_chains):
            return "underlying_without_listed_options"
        return "priceable"


def _headers(contact: str) -> dict:
    # The SEC asks callers to identify themselves; requests without a declared
    # contact are throttled or refused.
    return {"User-Agent": contact, "Accept-Encoding": "gzip, deflate"}


def fetch(url: str, *, contact: str, session: requests.Session,
          attempts: int = 5, pause: float = 0.12) -> requests.Response:
    """Fetch one EDGAR URL, backing off when the SEC throttles.

    Screening a week of filings means several hundred document requests, and
    EDGAR answers a burst with 503 or 429 rather than slowing down. Retrying
    with a lengthening wait is what the SEC's fair-access notice asks for, and
    it is honoured here: the wait doubles from the configured pause, and a
    Retry-After header wins over that if the response carries one.

    Only throttling and transport errors are retried. A 404 is a real answer
    about a document that is not there, so it is raised at once instead of
    being hidden behind four more requests.
    """
    delay = max(pause, 0.5)
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, headers=_headers(contact), timeout=90)
        except requests.RequestException as error:
            last = error
        else:
            if response.status_code not in (429, 503, 502, 504):
                response.raise_for_status()
                return response
            last = requests.HTTPError(f"{response.status_code} from {url}")
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
        if attempt < attempts - 1:
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"gave up on {url} after {attempts} attempts: {last}")


def to_text(raw_html: str) -> str:
    """Strip markup to searchable text, with entities and trademarks resolved.

    Entity decoding runs after the tags come out, so an entity that appears
    inside an attribute cannot inject text. Trademark marks are then dropped
    rather than turned into spaces: "Nasdaq-100(R) Index" has to collapse to
    "Nasdaq-100 Index" for the index-name patterns to match it, and several
    issuers put a mark on every index name in the title.
    """
    stripped = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw_html)
    stripped = _TAG.sub(" ", stripped)
    stripped = unescape(stripped)
    stripped = _MARKUP_NOISE.sub("", stripped)
    for character, replacement in _PUNCTUATION_FOLD:
        stripped = stripped.replace(character, replacement)
    return _WHITESPACE.sub(" ", stripped).strip()


def list_filings(
    cik: str, issuer: str, filing_dates: set[str], *, contact: str,
    session: requests.Session | None = None,
) -> list[Screen]:
    """Every 424B2 one issuer filed on the given dates."""
    http = session or requests.Session()
    response = fetch(SUBMISSIONS_URL.format(cik=cik), contact=contact, session=http)
    recent = response.json()["filings"]["recent"]

    out = []
    for index, form in enumerate(recent["form"]):
        if form != "424B2" or recent["filingDate"][index] not in filing_dates:
            continue
        accession = recent["accessionNumber"][index]
        document = recent["primaryDocument"][index]
        out.append(
            Screen(
                issuer=issuer, cik=cik, accession=accession, document=document,
                filing_date=recent["filingDate"][index],
                url=ARCHIVE_URL.format(
                    cik_int=int(cik), accession=accession.replace("-", ""), document=document
                ),
            )
        )
    return out


def identify_underlyings(text: str) -> tuple[list[str], list[str | None]]:
    """Pull the underlying codes out of the declaration block and map them."""
    match = re.search(
        r"(?:Underliers?|Indices|Underlying Index(?:es)?|Reference Assets?|Underlying(?:s| Shares)?)"
        r"\s*:\s*(.{0,1200})",
        text, re.S,
    )
    block = match.group(1) if match else text[:6000]

    codes: list[str] = []
    for pattern in (BLOOMBERG_TICKER, QUOTED_INDEX):
        for found in pattern.finditer(block):
            code = found.group(1).upper()
            if code in {"INDEX", "THE", "US", "AND", "ETF", "FUND", "SYMBOL"}:
                continue
            if code not in codes:
                codes.append(code)

    if len(codes) < 2:
        # No usable ticker declaration. Fall back to the index names in the
        # title, which is how Citigroup identifies its underlyings.
        codes = _codes_from_names(text[:4000]) or codes

    chains: list[str | None] = []
    for code in codes:
        chains.append(INDEX_CODES.get(code, code if code.isalpha() and len(code) <= 5 else None))
    return codes, chains


def _codes_from_names(head: str) -> list[str]:
    """Index codes from full index names, most specific name first.

    Each match is blanked out of the working copy before the next pattern runs,
    so a broader name cannot match inside a narrower one that already matched.
    That is the whole defence against reading the Nasdaq-100 Technology Sector
    Index as the Nasdaq-100.
    """
    working = head
    found: list[tuple[int, str]] = []
    for pattern, code in _INDEX_NAME_PATTERNS:
        for match in pattern.finditer(working):
            found.append((match.start(), code))
        working = pattern.sub(lambda m: " " * len(m.group(0)), working)

    ordered: list[str] = []
    for _, code in sorted(found):
        if code not in ordered:
            ordered.append(code)
    return ordered


def screen_filing(entry: Screen, html: str) -> Screen:
    """Classify one filing from its text."""
    text = to_text(html)
    head = text[:5000]

    entry.is_preliminary = bool(PRELIMINARY.search(head)) or "prelim" in entry.document.lower()
    entry.is_worst_of = bool(WORST_OF.search(text))
    entry.has_contingent_coupon = bool(CONTINGENT_COUPON.search(text))

    range_match = ESTIMATED_VALUE_RANGE.search(text)
    point_match = ESTIMATED_VALUE.search(text)
    if range_match:
        entry.estimated_value_is_range = True
        entry.estimated_value = float(range_match.group(1).replace(",", ""))
    elif point_match:
        entry.estimated_value = float(point_match.group(1).replace(",", ""))

    automatic = bool(AUTOMATIC_CALL.search(text))
    discretionary = bool(ISSUER_CALL.search(text))
    entry.call_style = (
        "automatic" if automatic and not discretionary
        else "issuer_discretionary" if discretionary and not automatic
        else "both_mentioned" if automatic and discretionary
        else "none_found"
    )

    entry.underlying_codes, entry.underlying_chains = identify_underlyings(text)
    if len(entry.underlying_codes) < 2:
        entry.underlyings_are_single_names = bool(SINGLE_NAME_UNDERLYING.search(text[:4000]))
    return entry


def run(
    filing_dates: set[str],
    cache_dir: Path,
    *,
    contact: str,
    pause: float = 0.12,
) -> list[Screen]:
    """Screen every 424B2 filed by the tracked issuers on the given dates."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    entries: list[Screen] = []
    for issuer, cik in ISSUER_CIKS.items():
        entries.extend(list_filings(cik, issuer, filing_dates, contact=contact, session=session))
        time.sleep(pause)
    print(f"  {len(entries)} 424B2 filings across {len(ISSUER_CIKS)} issuers")

    screened = []
    for entry in entries:
        cached = cache_dir / f"{entry.accession.replace('-', '')}_{entry.document}.gz"
        if cached.exists() and cached.stat().st_size > 500:
            html = gzip.open(cached, "rb").read().decode("utf-8", "replace")
        else:
            response = fetch(entry.url, contact=contact, session=session, pause=pause)
            with gzip.open(cached, "wb") as handle:
                handle.write(response.content)
            html = response.text
            time.sleep(pause)
        screened.append(screen_filing(entry, html))

    finals = [s for s in screened if s.is_final_worst_of]
    priceable = [s for s in screened if s.is_priceable]
    print(f"  {len(finals)} final worst-of notes with a disclosed point estimate")
    print(f"  {len(priceable)} of those reference underlyings with free listed options")

    blocked: dict[str, int] = {}
    for entry in finals:
        if entry.is_priceable:
            continue
        for code, chain in zip(entry.underlying_codes, entry.underlying_chains):
            if chain is None:
                blocked[code] = blocked.get(code, 0) + 1
    if blocked:
        ranked = sorted(blocked.items(), key=lambda kv: -kv[1])
        print("  underlyings with no free option market: " +
              ", ".join(f"{code} ({count})" for code, count in ranked[:8]))
    return screened


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dates", nargs="+", default=["2026-10-07", "2026-10-08", "2026-10-09"])
    parser.add_argument("--cache", type=Path, default=Path("data/raw/edgar_cache"))
    parser.add_argument(
        "--contact", required=True,
        help="name and email, which the SEC requires callers to declare",
    )
    parser.add_argument("--out", type=Path, default=Path("data/processed/edgar_screen.json"))
    parser.add_argument(
        "--pause", type=float, default=0.12,
        help="seconds between requests; raise it if EDGAR starts answering 503",
    )
    args = parser.parse_args()

    results = run(set(args.dates), args.cache, contact=args.contact, pause=args.pause)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps([r.as_record() for r in results], indent=1))
    print(f"wrote {args.out}")
