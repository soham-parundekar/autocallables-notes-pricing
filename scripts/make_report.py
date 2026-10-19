"""Render reports/report.md from the template, filling the numbers from the run.

The report's prose lives in reports/report_template.md with placeholders written
as {{name}}. Every name has to resolve against the figures computed in
src/analysis/results.py, and an unresolved placeholder stops the build instead
of reaching the page. That is the point of the indirection: a figure quoted in
the report text is a figure that came out of the run, and there is no path by
which one can be typed in by hand and go unnoticed.

Flags worth knowing about:

    --require 6   refuse to render unless all six notes are priced, which is
                  what the final build uses. Without it a partial run renders,
                  with its ranges computed over whatever is there, which is
                  useful while the long-dated notes are still going.
    --check       render to memory and report what would change, touching
                  nothing. Used by the audit pass.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.results import figures, load_results

PLACEHOLDER = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")

# The template opens with a note to whoever edits it. That note is about the
# template, not about the research, so it is stripped on the way out rather
# than shipped at the top of the report.
TEMPLATE_NOTE = re.compile(r"\A\s*<!--.*?-->\s*", re.S)

# Figures whose job is to assert something the prose claims in words. If one of
# these comes back anything other than "yes", the sentence it supports has
# become false and the report must not be rendered as though it were still true.
ASSERTIONS = {
    "gap_all_negative": "every model value is below its disclosed value",
    "optimal_spread_all_negative": "every solved optimal-exercise spread is negative",
    "flat_vol_all_positive": "a flat vol raises every note's value",
    "timing_all_negative": "the timing adjustment lowers every note's value",
    "corr_long_all": "the investor is long correlation on every note",
}


def reflow(text: str, width: int = 79) -> str:
    """Re-wrap body paragraphs after substitution.

    A placeholder and the figure that replaces it are rarely the same length, so
    filling the template leaves prose with lines of wildly uneven width. It
    renders the same either way, but the markdown source is meant to be read and
    diffed, and a one-word line next to a ninety-character one makes a review
    harder than it needs to be.

    Tables, fenced code, indented blocks, headings, list items and block quotes
    are passed through untouched: re-wrapping a table row breaks it, and
    re-wrapping a list item silently turns the continuation into a new
    paragraph.
    """
    out: list[str] = []
    paragraph: list[str] = []
    in_fence = False

    def flush() -> None:
        if not paragraph:
            return
        out.extend(textwrap.wrap(
            " ".join(paragraph), width=width,
            break_long_words=False, break_on_hyphens=False,
        ))
        paragraph.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            in_fence = not in_fence
            out.append(line)
            continue
        # A list marker is a dash or asterisk followed by space; "**Bold lead."
        # opens a paragraph and must still be wrapped, which is why this tests
        # for the space rather than the character.
        is_list_item = bool(re.match(r"^[-*+]\s", stripped)) or stripped in {"-", "*"}
        verbatim = (
            in_fence
            or not stripped
            or line.startswith(("    ", "\t"))
            or stripped.startswith(("|", "#", ">", "<!--"))
            or is_list_item
            or re.match(r"^\d+\.\s", stripped)
        )
        if verbatim:
            flush()
            out.append(line)
            continue
        paragraph.append(stripped)
    flush()
    return "\n".join(out)


def render(template: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute the placeholders, collecting the names that did not resolve."""
    missing: list[str] = []

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            missing.append(name)
            return match.group(0)
        return values[name]

    return PLACEHOLDER.sub(replace, template), missing


def main(root: Path, *, require: int | None, check: bool) -> int:
    template_path = root / "reports/report_template.md"
    target = root / "reports/report.md"
    template = template_path.read_text()

    notes, meta = load_results(root, require=require)
    values = figures(notes, meta, root)

    broken = {k: values[k] for k, claim in ASSERTIONS.items()
              if values.get(k, "NO") != "yes"}
    if broken:
        for key in broken:
            print(f"  the prose claims {ASSERTIONS[key]}, and the results no longer "
                  f"support it ({key}={values.get(key)})", file=sys.stderr)
        print("fix the report text before rendering", file=sys.stderr)
        return 1

    rendered, missing = render(TEMPLATE_NOTE.sub("", template), values)
    rendered = reflow(rendered)
    if missing:
        for name in sorted(set(missing)):
            print(f"  no figure named {name}", file=sys.stderr)
        print(f"{len(set(missing))} placeholder(s) unresolved; nothing written",
              file=sys.stderr)
        return 1

    if check:
        current = target.read_text() if target.exists() else ""
        if current == rendered:
            print(f"{target.relative_to(root)} is current "
                  f"({len(notes)} notes, snapshot {meta.get('snapshot')})")
            return 0
        print(f"{target.relative_to(root)} is stale; run make report")
        return 1

    target.write_text(rendered)
    print(f"wrote {target.relative_to(root)} from {len(notes)} notes "
          f"({', '.join(sorted(notes))}), snapshot {meta.get('snapshot')}")
    if meta.get("duplicates"):
        print(f"  note: {sorted(set(meta['duplicates']))} appeared in more than one "
              f"result file; the later file was used")
    if values.get("attribution_available") != "yes":
        print("  note: call_attribution.json is absent, so the attribution section "
              "renders without its numbers")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--require", type=int, default=None,
                        help="refuse to render unless this many notes are priced")
    parser.add_argument("--check", action="store_true",
                        help="report whether report.md matches the saved results")
    args = parser.parse_args()
    raise SystemExit(main(args.root, require=args.require, check=args.check))
