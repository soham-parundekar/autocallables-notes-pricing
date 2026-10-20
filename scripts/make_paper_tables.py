"""Build the manuscript's LaTeX tables from the frozen results.

The report's tables and the paper's tables describe the same run, so they are
built from the same source rather than transcribed from one into the other.
Everything here reads `src.analysis.results`, which is the layer that merges the
saved result files and derives the headline sample; nothing in this file knows
how to compute a price.

That matters more for the paper than for the report. A number typed into a
manuscript has no way back to the run that produced it, and the one time a
figure was written by hand in this project it was wrong. So the tables are
generated, the manuscript inputs them, and a stale table is a build failure
rather than a quiet error in print.

One table per file under paper/tables/, each a float with its own note block.
The manuscript pulls them in with \\input, so a regenerated table reaches the
PDF on the next compile with no editing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.results import barrier_coverage, figures, load_results, short_issuer

# LaTeX-significant characters that appear in this project's data: percentages
# in the terms, dollar amounts in the captions, underscores in FRED series
# names. Braces and backslashes are not escaped because the values here never
# contain them and the table bodies are assembled from literal LaTeX.
ESCAPES = {"%": r"\%", "$": r"\$", "&": r"\&", "_": r"\_", "#": r"\#"}


def tex(value: object) -> str:
    text = str(value)
    for char, replacement in ESCAPES.items():
        text = text.replace(char, replacement)
    return text


def signed(value: float, places: int = 2) -> str:
    """A number with a typographic minus rather than a hyphen.

    A hyphen set in running text is noticeably shorter than a minus sign and
    reads as a dash in a column of figures, so the sign is set in math mode and
    the digits are not: inside math mode the thousands comma picks up extra
    spacing.
    """
    sign = "$-$" if value < 0 else ""
    return f"{sign}{abs(value):,.{places}f}"


def table(*, label: str, caption: str, spec: str, header: list[str],
          rows: list[list[str]], note: str = "", position: str = "tbp") -> str:
    """One booktabs float. Header cells are set in the running weight.

    apacite and booktabs together make bold headers look heavier than the rule
    above them, so the header is plain and the rules carry the structure, which
    is the convention in the author's previous paper.
    """
    body = [
        f"\\begin{{table}}[{position}]",
        "\\centering",
        f"\\caption{{{caption}}}",
        f"\\label{{{label}}}",
        "\\small",
        f"\\begin{{tabular}}{{{spec}}}",
        "\\toprule",
        " & ".join(header) + " \\\\",
        "\\midrule",
    ]
    body += [" & ".join(row) + " \\\\" for row in rows]
    body += ["\\bottomrule", "\\end{tabular}"]
    if note:
        body.append(f"\\notetext{{{note}}}")
    body.append("\\end{table}")
    return "\n".join(body) + "\n"


def census(root: Path) -> str:
    """Why 1,086 filings in one week yield the number of notes that they do.

    The stages are read from the screen's own verdicts rather than restated, so
    a rerun that reclassifies a filing moves the table with it.
    """
    records = json.loads((root / "data/processed/edgar_screen.json").read_text())
    stages = [
        ("preliminary", "Preliminary supplement; terms not yet fixed"),
        ("not_worst_of", "Not a worst-of structure"),
        ("no_contingent_coupon", "No contingent coupon"),
        ("no_estimated_value", "No estimated value disclosed"),
        ("single_name_or_fund_underlyings", "Single-name or fund underlyings"),
        ("underlying_without_listed_options", "An underlying with no listed options"),
        ("priceable", "Priceable from public option quotes"),
    ]
    counts = {name: sum(1 for r in records if r["screen_stage"] == name) for name, _ in stages}
    total = len(records)
    rows = [[reason, f"{counts[name]:,}", f"{counts[name] / total * 100:.1f}"]
            for name, reason in stages]
    rows.append(["\\midrule \\textit{Total 424B2 filings}",
                 f"\\textit{{{total:,}}}", "\\textit{100.0}"])
    priceable = counts["priceable"]
    finals = sum(1 for r in records if r.get("is_final_worst_of"))
    share = f"{priceable / finals * 100:.0f}" if finals else "n/a"
    return table(
        label="tab:census",
        caption="Every 424B2 filed in the pricing week, by the reason it does or "
                "does not reach the sample",
        spec="l r r",
        header=["Disposition", "Filings", "Share (\\%)"],
        rows=rows,
        note=f"\\textit{{Notes:}} All 424B2 filings retrieved from EDGAR full-text "
             f"search for the week of the snapshot. Each filing is assigned to the "
             f"first stage it fails, so the rows partition the population. A "
             f"preliminary supplement is excluded because its coupon, trigger and "
             f"estimated value are still ranges. The last three rows are the final "
             f"worst-of notes with a contingent coupon and a disclosed estimated "
             f"value, {finals:,} in all, of which {priceable:,} ({share}\\%) can be "
             f"priced from public option quotes; the six notes studied here are drawn "
             f"from those {priceable:,}. Source: "
             f"\\texttt{{src/data/edgar\\_notes.py}}.")


def note_terms(root: Path, results: dict, figs: dict) -> str:
    config = {n["id"]: n for n in yaml.safe_load((root / "config/notes.yaml").read_text())["notes"]}
    coverage = barrier_coverage(root)
    rows = []
    for note_id in sorted(results):
        note = config[note_id]
        base = results[note_id]["base"]
        legs = ", ".join(u["ticker"] for u in note["underlyings"])
        marker = "" if coverage.get(note_id, {}).get("fully_covered", True) else "\\textsuperscript{a}"
        # The formatted strings come from figures(), which is the same layer the
        # report quotes, so a table cell and a sentence cannot disagree. Only
        # the two barriers are read from the terms file, as fractions.
        rows.append([
            f"{note_id}{marker}",
            tex(short_issuer(note["issuer"])),
            tex(legs),
            tex(figs[f"tenor_{note_id}"]),
            tex(figs[f"coupon_{note_id}"]),
            f"{note['coupon']['barrier'] * 100:.2f}",
            f"{note['downside']['trigger'] * 100:.2f}",
            tex(figs[f"disclosed_{note_id}"]),
            tex(figs[f"commission_{note_id}"]),
        ])
    return table(
        label="tab:terms",
        caption="The six notes: underlyings, terms and the figures the issuer discloses",
        spec="l l l r r r r r r",
        header=["Note", "Issuer", "Underlyings", "Tenor", "Coupon",
                "Barrier", "Trigger", "Est.\\ value", "Commission"],
        rows=rows,
        note="\\textit{Notes:} Tenor in years from pricing date to stated maturity. "
             "Coupon is the contingent rate per annum in per cent, paid only when "
             "every underlying is at or above its coupon barrier on an observation "
             "date. \\textit{Barrier} is that coupon threshold and \\textit{Trigger} "
             "the principal-repayment threshold, both as a percentage of the initial "
             "level. Estimated value and selling commission are per \\$1,000 of "
             "principal, as disclosed in the pricing supplement. "
             "\\textsuperscript{a}\\,Held out of the ranges quoted in the text: no "
             "fitted slice past one year carries a quoted strike at this note's "
             "trigger on one leg, so its downside is extrapolated rather than "
             "observed. Source: the filings listed in Appendix~\\ref{app:terms}.")


def validation(root: Path, figs: dict) -> str:
    """Does the engine return the vanilla prices it was calibrated to?

    The error is reported against the quoted bid-ask spread rather than on its
    own, because an error is only large or small relative to the price
    uncertainty in the market it came from. One surface fails that comparison
    and the column is what shows it.
    """
    rows = []
    with (root / "data/processed/reprice_volpoints.csv").open() as handle:
        for row in csv.DictReader(handle):
            rows.append([
                tex(row["ticker"]),
                f"{int(row['n']):,}",
                f"{float(row['inside_pct']):.1f}",
                f"{float(row['err_volpts_med']):.3f}",
                f"{float(row['spread_volpts_med']):.3f}",
                f"{float(row['ratio']):.2f}",
            ])
    return table(
        label="tab:validation",
        caption="Repricing error against quoted bid-ask width, by surface, in "
                "implied volatility points",
        spec="l r r r r r",
        header=["Surface", "Options", "Inside spread (\\%)",
                "Median error", "Median spread", "Error/spread"],
        rows=rows,
        note=f"\\textit{{Notes:}} Every option used in the calibration is repriced "
             f"through the local-volatility engine and the result converted back to "
             f"implied volatility, so the error is in the units the market quotes. "
             f"\\textit{{Inside spread}} is the share of repriced options falling "
             f"within the quoted bid-ask. The final column is the median error "
             f"divided by the median quoted bid-ask width. Error is measured from "
             f"the mid and the quoted band runs half a width either side of it, so "
             f"a ratio below one half puts the model inside the band the market "
             f"itself prices in. Four surfaces return "
             f"{figs['reprice_others_err_over_spread_max']} or better; the SPX "
             f"surface returns {figs['reprice_worst_err_over_spread']}, on a quoted "
             f"width {figs['reprice_spread_ratio']} times narrower than the next "
             f"tightest here, which \\S\\ref{{sec:validation}} attributes to that "
             f"market's liquidity rather than to the fit. Source: "
             f"\\texttt{{src/vol/reprice\\_check.py}}.")


def results_table(results: dict, figs: dict, root: Path) -> str:
    coverage = barrier_coverage(root)
    config = {n["id"]: n for n in yaml.safe_load((root / "config/notes.yaml").read_text())["notes"]}
    rows = []
    for note_id in sorted(results):
        base = results[note_id]["base"]
        marker = "" if coverage.get(note_id, {}).get("fully_covered", True) else "\\textsuperscript{a}"
        # Both operands are frozen, so the gap and its percentage are derived
        # here rather than parsed back out of the formatted strings.
        disclosed = config[note_id]["disclosed_estimated_value"]
        gap = base["value"] - disclosed
        rows.append([
            f"{note_id}{marker}",
            f"{base['value']:,.2f}",
            f"{base['stderr']:.2f}",
            f"{disclosed:,.2f}",
            signed(gap),
            signed(gap / disclosed * 100),
        ])
    return table(
        label="tab:results",
        caption="Model value against the disclosed estimated value, per \\$1,000 "
                "of principal",
        spec="l r r r r r",
        header=["Note", "Model value", "s.e.", "Disclosed", "Gap", "Gap (\\%)"],
        rows=rows,
        note=f"\\textit{{Notes:}} Model value is the mean discounted payoff over "
             f"{figs['paths']} paths with a European control variate, at a zero "
             f"funding spread over the Treasury curve, with the issuer's call "
             f"exercised by the Longstaff--Schwartz rule. The standard error is of "
             f"that mean. Gap is model value less disclosed value, so a negative "
             f"gap means the independent valuation lands below the figure the issuer "
             f"discloses. Every note shows one. "
             f"\\textsuperscript{{a}}\\,Excluded from the ranges quoted in the text; "
             f"see Table~\\ref{{tab:terms}}.")


def spreads(results: dict, figs: dict) -> str:
    rows = []
    for note_id in sorted(results):
        record = results[note_id]
        optimal = record.get("implied_spread_optimal_exercise") or {}
        no_call = record.get("implied_spread_no_early_call") or {}

        def cell(spread: dict) -> str:
            if not spread:
                return "n/a"
            if not spread.get("bracketed"):
                return "no solution"
            return f"{spread['spread'] * 1e4:+,.0f}"

        no_call_value = (f"{no_call['value_at_zero']:,.2f}"
                         if no_call.get("value_at_zero") is not None else "n/a")
        rows.append([note_id, tex(figs[f"disclosed_{note_id}"]),
                     f"{record['base']['value']:,.2f}", cell(optimal),
                     no_call_value, cell(no_call)])
    return table(
        label="tab:spreads",
        caption="The funding spread that would reconcile model and disclosed "
                "value, under two assumptions about the call",
        spec="l r r r r r",
        header=["Note", "Disclosed", "Model", "Spread (bp)",
                "Model, no call", "Spread (bp)"],
        rows=rows,
        note=f"\\textit{{Notes:}} Each spread is the constant addition to the "
             f"Treasury discount curve that sets the model value equal to the "
             f"disclosed value, solved on a common set of paths. A funding spread "
             f"should be positive and wider than the issuer's own senior bonds: the "
             f"A-rated US corporate option-adjusted spread was "
             f"{figs['attribution_spread_bp']}\\,bp on the pricing date. Under "
             f"optimal exercise every solved spread is negative, from "
             f"{figs['optimal_spread_min_bp']}\\,bp to "
             f"{figs['optimal_spread_max_bp']}\\,bp, which would require the issuer "
             f"to fund below Treasuries. \\textit{{No solution}} means the disclosed "
             f"value lies outside the bracket searched; "
             f"\\S\\ref{{sec:results}} reports where that note's crossing lies. The "
             f"auto-callable note has no no-call column: its redemption is "
             f"contractual, so removing it describes a different security rather "
             f"than a different modelling assumption.")


def decomposition(results: dict) -> str:
    """The note split the way a desk would book it.

    The parts are simulated under one set of draws so that they add up, which
    means this table's last column is a coarser estimate of the headline value
    than Table~\\ref{tab:results} reports. The note says so, because two
    numbers for the same quantity otherwise read as a contradiction.
    """
    rows = []
    for note_id in sorted(results):
        d = results[note_id]["decomposition"]
        rows.append([
            note_id,
            signed(d["funding_bond"]),
            signed(d["coupon_strip"]),
            signed(-d["worst_of_put_cost"]),
            signed(-d["issuer_call_cost"]),
            signed(d["value_without_call"]),
            signed(d["value_with_call"]),
        ])
    risk_paths = {r["risk_paths"] for r in results.values()}
    base_paths = {r["base"]["paths"] for r in results.values()}
    basis = ""
    if len(risk_paths) == 1 and len(base_paths) == 1:
        basis = (f" The decomposition runs on {risk_paths.pop():,} paths with no "
                 f"control variate, because every part has to be simulated under the "
                 f"same draws for the parts to add up; Table~\\ref{{tab:results}} "
                 f"reports the headline estimator at {base_paths.pop():,} paths with "
                 f"the control variate. The last column is therefore a coarser "
                 f"estimate of the same quantity, not a competing one.")
    return table(
        label="tab:decomposition",
        caption="The note decomposed into the positions a desk would book, "
                "per \\$1,000 of principal",
        spec="l r r r r r r",
        header=["Note", "Funding bond", "Coupon strip", "Worst-of put",
                "Issuer's call", "Without call", "With call"],
        rows=rows,
        note="\\textit{Notes:} The investor is long a funding bond and a coupon "
             "strip and short a worst-of put, and signs are from the investor's side. "
             "Where redemption is the issuer's decision the call is a cost to the "
             "investor and the column is negative. On the one auto-callable note it is "
             "positive: redemption is mechanical, returns principal early in states "
             "where the note would otherwise stay exposed, and so favours the holder. "
             "\\textit{Without call} is the sum of the first three columns and "
             "\\textit{With call} subtracts the fourth." + basis)


def coverage(root: Path, results: dict) -> str:
    """Appendix: how many fitted slices past one year quote each note's trigger.

    This is the measurement the headline sample's partition rests on, so it is
    published leg by leg rather than summarised. A reader who thinks the
    threshold is too lenient can apply a stricter one to these counts.
    """
    cov = barrier_coverage(root)
    rows = []
    for note_id in sorted(results):
        record = cov.get(note_id)
        if not record:
            continue
        legs = record["legs"]
        cells = ", ".join(f"{t} {v['covered']}/{v['slices']}" for t, v in sorted(legs.items()))
        rows.append([
            note_id,
            signed(record["k_trigger"], 3),
            tex(cells),
            "yes" if record["fully_covered"] else "\\textbf{no}",
        ])
    return table(
        label="tab:coverage",
        caption="Quoted strike coverage at each note's downside trigger, by leg",
        spec="l r l c",
        header=["Note", "$\\logm$ at trigger", "Covered slices past one year, by leg",
                "Fully covered"],
        rows=rows,
        note="\\textit{Notes:} The trigger is expressed as log-moneyness relative to "
             "the initial level, so $-0.431$ is a trigger at 65 per cent of it. For "
             "each leg the entry counts the fitted slices with maturity beyond one "
             "year whose quoted strikes reach down to that level, against the number "
             "of such slices. A leg at 0 prices that note's entire downside past the "
             "first year from an extrapolated wing rather than from a quote. One note "
             "fails on one leg and is held out of every range quoted in the text, "
             "though its own figures are reported throughout.",
        position="htbp")


def candidate_inputs(root: Path, results: dict, figs: dict) -> str:
    """Each candidate explanation, with the move it would need and the move it gets."""
    config = {n["id"]: n for n in yaml.safe_load((root / "config/notes.yaml").read_text())["notes"]}
    rows = []
    for note_id in sorted(results):
        record = results[note_id]
        gap = record["base"]["value"] - config[note_id]["disclosed_estimated_value"]
        rows.append([
            note_id,
            signed(gap),
            signed(record["correlation"]["d_value_per_0.05"]),
            signed(record["flat_vol"]["difference"]),
            signed(record["timing"]["difference"]),
            signed(record["decomposition"]["issuer_call_cost"]),
        ])
    return table(
        label="tab:inputs",
        caption="Candidate explanations for the gap, one input at a time, "
                "per \\$1,000 of principal",
        spec="l r r r r r",
        header=["Note", "Gap to close", "+0.05 correlation",
                "Flat vol", "Timing", "Call cost"],
        rows=rows,
        note="\\textit{Notes:} Each column is the change in model value from "
             "moving one input while the rest are held at their calibrated values, "
             "on a common set of paths. \\textit{+0.05 correlation} raises every "
             "pairwise correlation, confirming the investor is long correlation on "
             "every note. \\textit{Flat vol} replaces the fitted surface with a "
             "single at-the-money volatility per leg. It raises every value, and by "
             "more than the gap on five of the six, so the shape of the surface is "
             "the one input measured here whose effect both runs toward the disclosed "
             "value and is large enough to span the difference. \\textit{Timing} "
             "aligns the surface to the pricing date rather than the snapshot date "
             "and lowers every value, widening the gap. \\textit{Call cost} is what the issuer's option to redeem is "
             "worth to the issuer, from the decomposition in "
             "Table~\\ref{tab:decomposition}. No single column closes the gap in "
             "the first; \\S\\ref{sec:inputs} takes them in turn.")


def main(root: Path) -> int:
    results, meta = load_results(root)
    figs = figures(results, meta, root)
    out = root / "paper/tables"
    out.mkdir(parents=True, exist_ok=True)

    built = {
        "census.tex": census(root),
        "terms.tex": note_terms(root, results, figs),
        "validation.tex": validation(root, figs),
        "results.tex": results_table(results, figs, root),
        "spreads.tex": spreads(results, figs),
        "decomposition.tex": decomposition(results),
        "inputs.tex": candidate_inputs(root, results, figs),
        "coverage.tex": coverage(root, results),
    }
    banner = ("% Generated by scripts/make_paper_tables.py from the frozen results.\n"
              "% Do not edit by hand; the next build overwrites it.\n")
    for name, body in built.items():
        (out / name).write_text(banner + body)

    print(f"wrote {len(built)} tables to {out.relative_to(root)}:")
    for name in built:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    raise SystemExit(main(parser.parse_args().root))
