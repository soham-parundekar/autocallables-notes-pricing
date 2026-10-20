"""Build the manuscript's .bib from the verified citation bank.

Typing a bibliography by hand is how wrong years and dropped co-authors get
into print. Clearing the verification queue for this paper caught six such
errors in metadata that search indexes had returned, including a 2023 that was
really 2025 and a five-author paper that an index rendered as four. None of
those would have been visible in a finished PDF.

So the .bib is generated rather than written. The citation bank holds the DOIs
and the verification status; this script asks Crossref for the authoritative
fields and emits the entries from the answer. An entry the bank has not marked
verified is skipped and named, so an unverified source cannot reach the
bibliography by being quietly typed in.

Crossref is queried live. With no network the script reuses the cached
responses under paper/.bibcache/ and says which entries came from cache, since
a bibliography built from a stale cache should be visibly that.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import unicodedata
import time
from pathlib import Path

CONTACT = "autocallable-pricer (mailto:parundekarsoham@gmail.com)"

# Crossref's type vocabulary mapped onto the BibTeX entry types apacite expects.
ENTRY_TYPE = {
    "journal-article": "article",
    "posted-content": "misc",
    "book": "book",
    "monograph": "book",
    "book-chapter": "incollection",
    "proceedings-article": "inproceedings",
    "dissertation": "phdthesis",
    "report": "techreport",
}


def crossref(doi: str, cache_dir: Path, *, offline: bool) -> tuple[dict | None, str]:
    """Return (record, provenance). Provenance is 'crossref' or 'cache'."""
    cached = cache_dir / (re.sub(r"[^A-Za-z0-9]+", "_", doi) + ".json")
    if not offline:
        result = subprocess.run(
            ["curl", "-sS", "-A", CONTACT, "--max-time", "40",
             f"https://api.crossref.org/works/{doi}"],
            capture_output=True, text=True,
        )
        try:
            record = json.loads(result.stdout)["message"]
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(record))
            return record, "crossref"
        except Exception:
            pass
    if cached.exists():
        return json.loads(cached.read_text()), "cache"
    return None, "unavailable"


def bibtex_key(record: dict) -> str:
    """Author surname plus year, which is what a reader expects to type.

    The surname is ASCII-folded first. Stripping non-ASCII without folding
    turned Jorgensen into "Jrgensen", which is a key nobody would guess.
    """
    authors = record.get("author") or []
    surname = authors[0].get("family", "Anon") if authors else "Anon"
    return ascii_key(surname) + str(issue_year(record) or "nd")


# Letters that are their own character rather than an accented base, so NFKD
# leaves them alone and a plain ASCII strip deletes them outright. Without this
# Jorgensen came out as "Jrgensen".
TRANSLITERATE = str.maketrans({
    "\u00f8": "o", "\u00d8": "O", "\u00e6": "ae", "\u00c6": "Ae",
    "\u00e5": "a", "\u00c5": "A", "\u0142": "l", "\u0141": "L",
    "\u00df": "ss", "\u0111": "d", "\u0110": "D", "\u00f0": "d", "\u00fe": "th",
})


def ascii_key(name: str) -> str:
    folded = unicodedata.normalize(
        "NFKD", name.translate(TRANSLITERATE)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z]", "", folded) or "Anon"


def issue_year(record: dict) -> int | None:
    """Prefer the printed issue year over the online-first year.

    Both appear in Crossref and they differ often enough to matter: one source
    in this bank is online 2018 and in print 2019, another online 2020 and in
    print 2022. The printed year is what a reader will find cited elsewhere.
    """
    for field in ("published-print", "issued", "published-online", "created"):
        parts = (record.get(field) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


# apacite renders titles in APA sentence case, which means it lowercases every
# word after the first. Two kinds of capital must survive that: the one opening
# a new sentence inside a title ("Fairly stated? Evidence from...") and proper
# nouns and acronyms. BibTeX preserves any capital wrapped in braces, so those
# are braced before the entry is written.
PROPER_NOUNS = (
    "US", "U.S.", "SEC", "CFR", "SVI", "DAX", "NSE", "ETF", "OIS", "APA",
    "Monte", "Carlo", "German", "Germany", "Danish", "Denmark", "American",
    "European", "Bermudan", "Dupire", "Heston", "Black", "Scholes",
    "Longstaff", "Schwartz", "Regulation", "Act", "Security", "Exchange",
    "Commission", "Electronic", "Code", "Federal", "Regulations",
)


def protect_capitals(title: str) -> str:
    words = title.split(" ")
    out, new_sentence = [], False
    for index, word in enumerate(words):
        core = word.strip("(),.;:?!")
        needs_brace = (
            new_sentence and word[:1].isupper()
            or core in PROPER_NOUNS
            or (len(core) > 1 and core.isupper())
        )
        if needs_brace and index > 0 and "{" not in word:
            word = word[:1] + "{" + word[1:2] + "}" + word[2:] if False else "{" + word + "}"
        out.append(word)
        new_sentence = core.endswith(("?", "!", ".", ":")) or word.endswith(("?", "!", ":"))
    return " ".join(out)


def escape(text: str) -> str:
    """Protect the characters that silently break a BibTeX build."""
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
    # Publishers deposit titles with their own footnote markers attached.
    text = text.translate({ord(c): None for c in "\u2606\u2605\u2020\u2021\u00a7"})
    for char in ("&", "%", "$", "#", "_"):
        text = text.replace(char, "\\" + char)
    return re.sub(r"\s+", " ", text).strip()


# Source types whose author is an institution rather than a person.
INSTITUTIONAL_TYPES = {"regulatory guidance", "regulation", "official report",
                       "exchange document", "statistical agency"}


def protect_institution(name: str) -> str:
    """Wrap an institutional author so BibTeX reads it as one name.

    BibTeX splits an author list on the word "and". An institution with "and"
    inside its own name is therefore read as two authors, and apacite then
    initialises the halves: "US Securities and Exchange Commission" came out of
    the reference list as "Securities, U., & Commission, E.". A second pair of
    braces suppresses both the split and the first-name parsing.
    """
    return name if name.startswith("{") else "{" + name + "}"


def authors_field(record: dict) -> str:
    """Crossref's author list in BibTeX syntax, with affiliation noise removed.

    Some publishers deposit affiliations as author entries. The Frontiers of
    Mathematical Finance record for 10.3934/fmf.2022008 interleaves three people
    with four institutions, which rendered as a seven-author paper with
    departments as co-authors.

    An entry carrying only `name` is either an institutional author or one of
    those strays, and the record itself distinguishes them: if any entry has a
    family name, the people are the authors and the name-only entries are
    affiliations. If none does, the work is genuinely institutional.
    """
    people = [p for p in (record.get("author") or []) if p.get("family")]
    if people:
        return " and ".join(
            f"{p['family']}, {p['given']}" if p.get("given") else p["family"]
            for p in people)
    return " and ".join(
        protect_institution(p["name"])
        for p in (record.get("author") or []) if p.get("name"))


BANK_TYPE = {"preprint": "misc", "thesis": "phdthesis", "book chapter": "incollection",
             "regulatory guidance": "misc", "regulation": "misc"}


def render(record: dict, doi: str, bank_type: str = "", row: dict | None = None) -> str:
    # The bank's own classification wins. Crossref types an SSRN preprint as a
    # journal-article in a journal called "SSRN Electronic Journal", which would
    # present a preprint as peer-reviewed work in the reference list.
    kind = BANK_TYPE.get(bank_type.strip().lower()) or ENTRY_TYPE.get(record.get("type", ""), "misc")
    fields: list[tuple[str, str]] = []

    def add(name: str, value, *, pre_escaped: bool = False) -> None:
        if value:
            fields.append((name, str(value) if pre_escaped else escape(str(value))))

    # Crossref deposits some book chapters with no author at all; the bank
    # carries the monograph author in those cases.
    add("author", authors_field(record) or (row or {}).get("authors", "").rstrip("."))
    add("year", issue_year(record))
    add("title", protect_capitals(escape((record.get("title") or [""])[0])), pre_escaped=True)

    container = (record.get("container-title") or [""])[0]
    if kind == "article":
        add("journal", container)
        add("volume", record.get("volume"))
        add("number", record.get("issue"))
        add("pages", (record.get("page") or "").replace("-", "--"))
    elif kind == "incollection":
        add("booktitle", container)
        add("pages", (record.get("page") or "").replace("-", "--"))
        add("publisher", record.get("publisher"))
    elif kind in {"book", "phdthesis", "techreport"}:
        add("publisher", record.get("publisher"))
        add("institution", record.get("publisher"))
        isbn = record.get("ISBN")
        add("isbn", isbn[0] if isinstance(isbn, list) and isbn else isbn)
    else:
        add("howpublished", container or record.get("publisher"))
        add("note", container and f"Preprint, {container}" or None)

    add("doi", doi)
    body = ",\n  ".join(f"{name:9s} = {{{value}}}" for name, value in fields)
    key = ((row or {}).get("cite_key") or "").strip() or bibtex_key(record)
    return f"@{kind}{{{key},\n  {body}\n}}\n"


def render_from_bank(row: dict) -> str:
    """A verified source with no DOI, rendered from the bank's own fields."""
    author = row["authors"].rstrip(".")
    key = (row.get("cite_key") or "").strip() or (
        ascii_key(author.split(",")[0].split()[0]) + str(row["year"]))
    if row.get("source_type", "").strip().lower() in INSTITUTIONAL_TYPES:
        author = protect_institution(author)
    fields = [("author", escape(author)), ("year", escape(row["year"])),
              ("title", protect_capitals(escape(row["title"]))), ("howpublished", escape(row["venue"])),
              ("url", row["doi_or_id"].strip()), ("note", "Accessed 19 October 2026")]
    body = ",\n  ".join(f"{n:9s} = {{{v}}}" for n, v in fields if v)
    return f"@misc{{{key},\n  {body}\n}}\n"


def main(root: Path, *, offline: bool) -> int:
    bank = root / "references/paper_citation_bank.csv"
    rows = list(csv.DictReader(bank.open()))
    cache = root / "paper/.bibcache"
    target = root / "paper/references.bib"

    entries, skipped, from_cache, unreachable = [], [], [], []
    for row in rows:
        verification = row["verification"].lower()
        doi = row["doi_or_id"].strip()
        if "not verified" in verification or not verification.startswith("verified"):
            skipped.append((row["ref_id"], "not marked verified in the bank"))
            continue
        if not doi.startswith("10."):
            # Regulations and agency guidance carry no DOI and are cited by
            # title and URL. They are verified sources, so they belong in the
            # bibliography; they are built from the bank rather than Crossref.
            if doi.startswith("http"):
                entries.append((row["ref_id"], render_from_bank(row)))
            else:
                skipped.append((row["ref_id"], f"no DOI or URL ({doi[:40] or 'empty'})"))
            continue
        record, provenance = crossref(doi, cache, offline=offline)
        if record is None:
            unreachable.append((row["ref_id"], doi))
            continue
        if provenance == "cache":
            from_cache.append(row["ref_id"])
        entries.append((row["ref_id"], render(record, doi, row.get("source_type", ""), row)))
        if not offline:
            time.sleep(0.35)

    header = (
        "% Generated by scripts/make_bibliography.py from references/"
        "paper_citation_bank.csv.\n"
        "% Fields come from Crossref, not from hand transcription. Do not edit "
        "this file;\n% correct the citation bank and regenerate.\n\n"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(header + "\n".join(body for _, body in sorted(entries)))

    print(f"wrote {target.relative_to(root)} with {len(entries)} entries")
    if from_cache:
        print(f"  {len(from_cache)} served from cache: {', '.join(from_cache)}")
    for ref_id, why in skipped:
        print(f"  skipped {ref_id}: {why}")
    for ref_id, doi in unreachable:
        print(f"  COULD NOT REACH {ref_id} ({doi}); it is absent from the bibliography")
    return 1 if unreachable else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--offline", action="store_true",
                        help="use the cached Crossref responses instead of querying")
    args = parser.parse_args()
    sys.exit(main(args.root, offline=args.offline))
