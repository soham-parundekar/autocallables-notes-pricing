"""Check every number in the manuscript against the frozen results.

A figure typed into a paper has no link back to the run that produced it. This
project has already caught itself quoting a value that had never been computed,
reading a quote count off a chart instead of out of the data, and reporting an
issuer count that belonged to a different population. Each was found by hand.
Checking roughly two hundred numbers by hand once per draft is how the next one
survives to print.

So the check is mechanical. Every numeric literal in the section files is
extracted and matched against a corpus built from the saved results, the
configuration and the screening output. Anything that does not match is printed
with its surrounding words for a human to adjudicate.

Matching is on the printed form, not on a tolerance. A number rounded
differently from its source is exactly the kind of drift worth seeing, so
`27.92` matches a stored 27.9178 only because the corpus holds that value
formatted to two decimals as well as to three. Values that legitimately appear
in prose without a source - section numbers, a year, a count the reader can
verify by looking at a table - live in ALLOWED below, each with a reason.

Exit status is 1 when anything is unmatched, so this can gate a build.

What this does and does not catch
---------------------------------
It catches a number with no source anywhere in the project. That is the class
that matters most, and it earned its keep on the first run: four figures in the
SVI appendix turned out to exist only in source-code comments from an earlier
configuration, with no frozen artefact behind them, and one of those comments
disagreed with the fitted surface it described.

It does not catch a number that is valid somewhere being used in the wrong
place. Changing a 69 basis point benchmark to 70 passes, because 70 is a coupon
barrier elsewhere in the sample and membership is all this test checks. Binding
each figure to the specific claim it supports is the job of the claim-evidence
matrix, not of this script, and the two are complements rather than substitutes.

tests/test_paper_number_audit.py plants a value with no source and asserts the
audit fails, so the check cannot rot into one that passes because it is broken.
An earlier version of this script did exactly that: a fallback meant to accept
comma-formatted integers ran int() on every literal, so a planted 11.47 matched
the 11 in front of its decimal point and the audit reported the manuscript
clean.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.results import barrier_coverage, figures, load_results

# A numeric literal as the manuscript writes one: optional sign, digits with
# LaTeX thousands separators ({,} or ,), optional decimals, optional percent.
NUMBER = re.compile(r"(?<![A-Za-z0-9])(-|\$-\$)?(\d[\d,]*(?:\{,\}\d{3})*(?:\.\d+)?)")

# Numbers that are part of LaTeX machinery rather than claims about the world.
STRUCTURAL = re.compile(
    r"\\(?:section|subsection|subsubsection|paragraph|label|ref|eqref|cite[a-zA-Z]*|"
    r"input|includegraphics|vspace|hspace|textsuperscript|footnote)\b|"
    r"\{(?:tab|fig|sec|eq|app):[^}]*\}|\{[0-9]+(?:pt|em|ex|in|cm)\}"
)

ALLOWED = {
    # (printed form, why it needs no source in the results)
    "0": "sign and interval boundaries in prose",
    "1": "unit interval boundary, and 'one' written as a numeral in ranges",
    "2": "count of items named in the same sentence",
    "3": "count of items named in the same sentence",
    "4": "count of items named in the same sentence",
    "5": "count of items named in the same sentence",
    "6": "the sample size, stated throughout and verifiable from Table 2",
    "10": "count of quoted points, stated with its source in the text",
    "2012": "year of the SEC sample comment letter, cited",
    "2026": "year of the sample week, stated with its dates",
    "2009": "year of the whitepaper that originated the quasi-explicit "
            "reduction, recorded in references/references.md under REF-008",
    "424": "the form number, 424B2",
    "225": "part of the index name Nikkei 225, not a quantity",
    "256": "part of the algorithm name SHA-256, not a quantity",
    "2.2.4": "a section number inside a cited work",
    "1.0": "unit interval boundary",
    "0.05": "the correlation bump, a configured model input",
    "0.5": "one half, as a threshold stated in words",
    "1e-6": "solver tolerance, reported as an order of magnitude",
}

# Numbers the manuscript derives from frozen values rather than quoting. Each
# records the arithmetic so the derivation is auditable instead of waved
# through; a reader can check any line here against the sources it names.
DERIVED = {
    "285": "201 not-worst-of + 84 no-contingent-coupon, both rows of Table 1",
    "1260": "5.00 years x 252 steps per year, from the longest tenor and config",
    "44": "12.36 / 27.92, the extrapolation effect over the gap on the longest note",
    "250": "(0.252 / 0.016)^2 = 248, the squared total-variance ratio a "
           "least-squares objective weights by",
    "1.17": "11.41 / 972.10, N5's gap as a percentage of its disclosed value",
    "2.97": "27.92 / 938.90, N1's gap as a percentage of its disclosed value",
    "0.525": "5.25 per 1,000, N4's disclosed commission stated as a percentage "
             "of face, as its cover prints it",
    "12.64": "979.94 - 967.30, N6's flat-vol value less its disclosed value",
    "33.93": "972.83 - 938.90, N1's flat-vol value less its disclosed value",
    "4.87": "972.10 - 967.23, N5's disclosed value less its flat-vol value",
    "0.0245": "total variance of the fitted SPX slice at T = 0.94, at the money",
    "0.1546": "the same slice at its deepest quoted strike, k = -0.777",
    "6.3": "0.1546 / 0.0245, the total-variance ratio across that slice",
    "40": "6.3 squared, the weight a least-squares objective in total variance "
          "puts on the wing relative to the money",
}


def corpus(root: Path) -> set[str]:
    """Every number the frozen artefacts can justify, in printed forms."""
    values: set[float] = set()

    def take(x) -> None:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return
        # Saved diagnostics can carry NaN or an infinity; neither is a number
        # the manuscript could be quoting.
        if v != v or v in (float("inf"), float("-inf")):
            return
        values.add(v)
        values.add(abs(v))

    def walk(obj) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v)
        elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
            take(obj)
        elif isinstance(obj, str):
            for m in re.finditer(r"-?\d+(?:\.\d+)?", obj.replace(",", "")):
                take(m.group(0))

    results, meta = load_results(root)
    walk(results)
    walk(meta)
    walk(figures(results, meta, root))
    walk(barrier_coverage(root))
    for name in ("config/params.yaml", "config/notes.yaml"):
        walk(yaml.safe_load((root / name).read_text()))
    for name in ("data/processed/edgar_screen.json", "data/processed/surfaces.json",
                 "data/processed/call_attribution.json", "data/processed/proxy_risk.json",
                 "data/processed/extended_bracket.json"):
        path = root / name
        if path.exists():
            walk(json.loads(path.read_text()))
    for name in ("data/processed/reprice_volpoints.csv", "data/processed/proxy_summary.csv",
                 "data/processed/reprice_summary.csv"):
        path = root / name
        if path.exists():
            for row in csv.DictReader(path.open()):
                walk(row)

    # Screening stage counts are derived rather than stored, and the manuscript
    # quotes them, so they belong in the corpus the same way.
    screen = root / "data/processed/edgar_screen.json"
    if screen.exists():
        records = json.loads(screen.read_text())
        take(len(records))
        stages = {r["screen_stage"] for r in records}
        for stage in stages:
            take(sum(1 for r in records if r["screen_stage"] == stage))
        finals = [r for r in records if r.get("is_final_worst_of")]
        take(len(finals))
        take(sum(1 for r in finals if r.get("is_priceable")))
        take(len(finals) - sum(1 for r in finals if r.get("is_priceable")))
        take(len({r["issuer"] for r in records if r.get("issuer")}))
        # Counts the text derives from the screen, such as how many filings in a
        # stage reference one underlying.
        for stage in stages:
            group = [r for r in records if r["screen_stage"] == stage]
            codes = {}
            for record in group:
                for code, chain in zip(record["underlying_codes"], record["underlying_chains"]):
                    if chain is None:
                        codes[code] = codes.get(code, 0) + 1
            for count in codes.values():
                take(count)
            take(len(group))

    # A fraction in the configuration is routinely printed as a percentage in
    # the text: a 0.65 trigger is "65 per cent of the initial level". Without
    # this the audit flags every barrier and trigger in the paper.
    for value in list(values):
        if 0.0 <= value <= 1.0:
            values.add(value * 100.0)

    printed: set[str] = set()
    for value in values:
        for places in range(0, 5):
            printed.add(f"{value:.{places}f}")
            printed.add(f"{value:,.{places}f}")
        printed.add(str(value))
        if value == int(value):
            printed.add(str(int(value)))
            printed.add(f"{int(value):,}")
    return printed


def strip_latex(text: str) -> str:
    """Drop comments and the commands whose arguments are not claims."""
    out = []
    for line in text.splitlines():
        stripped = re.sub(r"(?<!\\)%.*$", "", line)
        stripped = STRUCTURAL.sub(" ", stripped)
        out.append(stripped)
    return "\n".join(out)


def audit(root: Path) -> list[str]:
    known = corpus(root)
    findings = []
    for path in sorted((root / "paper/sections").glob("*.tex")):
        text = strip_latex(path.read_text())
        for match in NUMBER.finditer(text):
            raw = match.group(2)
            printed = raw.replace("{,}", "").replace(",", "")
            if printed in known or raw in ALLOWED or printed in ALLOWED:
                continue
            if printed in DERIVED or printed.rstrip('0').rstrip('.') in DERIVED:
                continue
            # Accept the comma-formatted spelling of a known integer. This has
            # to exclude anything with a decimal part: int("11.47") is 11, and
            # 11 is in the corpus, so without the guard every wrong decimal
            # matched the integer in front of its point. A planted 11.47 passed
            # this audit until the guard was added.
            if printed.isdigit() and f"{int(printed):,}" in known:
                continue
            start = max(0, match.start() - 60)
            context = " ".join(text[start:match.end() + 40].split())
            findings.append(f"{path.name}  [{raw}]  …{context}…")
    return findings


def main(root: Path) -> int:
    findings = audit(root)
    print(f"Auditing {root / 'paper/sections'}\n")
    if not findings:
        print("Every number in the manuscript traces to a frozen result.")
        return 0
    print(f"{len(findings)} number(s) with no match in the frozen results:\n")
    for item in findings:
        print(f"  {item}")
    print("\nEach needs a source, a correction, or an entry in ALLOWED with a reason.")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    raise SystemExit(main(parser.parse_args().root))
