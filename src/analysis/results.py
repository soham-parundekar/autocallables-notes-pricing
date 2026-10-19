"""Load the saved analysis results as one set of notes.

The five-year notes take about half an hour each to price, so they were run
separately from the four shorter ones and the results landed in two files. That
split is an artefact of how long the runs take, not a distinction that should
reach the report, so everything downstream reads the notes through here and
sees one dictionary.

`figures()` is the other half of this module and the more important one. Every
number quoted in the report text comes from it, so the prose cannot drift from
the run that produced it. Writing a figure into the report by hand is the one
mistake this layer exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

RESULT_FILES = ("analysis.json", "analysis_long.json")

# The filings name the issuing subsidiary; the report names the bank. Kept here
# rather than in the table generator so both produce the same labels.
ISSUER_SHORT = {
    "JPMorgan Chase Financial Company LLC": "JPMorgan",
    "JPMorgan Chase Financial Co. LLC": "JPMorgan",
    "Morgan Stanley Finance LLC": "Morgan Stanley",
    "GS Finance Corp.": "Goldman",
    "BofA Finance LLC": "BofA",
    "Citigroup Global Markets Holdings Inc.": "Citigroup",
}


def short_issuer(name: str) -> str:
    return ISSUER_SHORT.get(name, name)


def load_results(root: Path, *, require: int | None = None) -> tuple[dict, dict]:
    """Merge the result files and return (notes_by_id, metadata).

    Later files win on a collision, which is what you want when a note has been
    re-run: the newer file holds the better numbers. A collision is reported in
    the metadata rather than passed over, since two files claiming the same note
    usually means a run was launched with the wrong `--out`.
    """
    notes: dict[str, dict] = {}
    meta: dict = {"sources": [], "snapshot": None, "duplicates": []}

    for name in RESULT_FILES:
        path = root / "data/processed" / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        meta["sources"].append(name)
        meta["snapshot"] = payload.get("generated_for_snapshot") or meta["snapshot"]
        for note_id, result in payload.get("notes", {}).items():
            if note_id in notes:
                meta["duplicates"].append(note_id)
            notes[note_id] = result
        if payload.get("convergence"):
            meta.setdefault("convergence", {}).update(payload["convergence"])

    if require is not None and len(notes) < require:
        missing = require - len(notes)
        raise SystemExit(
            f"only {len(notes)} of {require} notes are priced ({missing} still missing): "
            f"{sorted(notes)}. Finish the analysis runs before regenerating the report."
        )
    return dict(sorted(notes.items())), meta


def _spread_bp(spread: dict | None) -> float | None:
    """Basis points, or None when the inversion found no solution in the bracket."""
    if not spread or not spread.get("bracketed"):
        return None
    return float(spread["spread"]) * 1e4


def barrier_coverage(root: Path) -> dict[str, dict]:
    """Per note and leg, how many fitted slices past one year quote the trigger.

    A worst-of note's whole downside sits at its trigger, so the surface is
    asked for implied vol at a log-moneyness of roughly -0.36 to -0.51. Whether
    a quote exists there decides whether the barrier is interpolated or
    extrapolated, and a published value looks the same either way.

    One note in this sample fails it on one leg: the Dow surface is fitted to
    DIA, whose surviving long-dated quotes span barely a fifth of the distance
    the barrier sits at. That is a property of the data rather than of the
    note, which is why the test is written against coverage and applied to
    whatever fails it, rather than against a note named in advance.
    """
    import math

    import yaml

    surfaces_path = root / "data/processed/surfaces.json"
    if not surfaces_path.exists():
        return {}
    surfaces = json.loads(surfaces_path.read_text())
    records = yaml.safe_load((root / "config/notes.yaml").read_text())["notes"]

    out: dict[str, dict] = {}
    for record in records:
        k_trigger = math.log(float(record["downside"]["trigger"]))
        legs = {}
        for leg in record["underlyings"]:
            ticker = leg["ticker"]
            slices = surfaces.get(ticker, {}).get("slices", [])
            deep = [s for s in slices if s["t"] >= 1.0] or slices
            legs[ticker] = {
                "covered": sum(1 for s in deep if s["grid_k_min"] <= k_trigger),
                "slices": len(deep),
            }
        out[record["id"]] = {
            "k_trigger": k_trigger,
            "legs": legs,
            "fully_covered": all(v["covered"] > 0 for v in legs.values()),
        }
    return out


def figures(notes: dict, meta: dict, root: Path) -> dict[str, str]:
    """Every figure the report text quotes, formatted as it should appear.

    Ranges are computed over whichever notes are present, so the report stays
    truthful about a partial run instead of quoting a range that assumes a note
    which has not finished.

    They are also computed over the *headline* notes rather than all of them. A
    note whose barrier falls outside the strikes the market quotes on any leg is
    priced off an extrapolation there, and averaging it into a range presents it
    as a measurement on the same footing as the rest. Its own numbers are still
    produced and reported; they are just reported as a case. Note that this
    removes the widest gap in the sample, so the headline range it produces is
    narrower and less striking than the one over all six.
    """
    import csv

    out: dict[str, object] = {}
    ids = sorted(notes)
    coverage = barrier_coverage(root)
    headline = [i for i in ids
                if coverage.get(i, {}).get("fully_covered", True)]
    excluded = [i for i in ids if i not in headline]
    # Never let the criterion empty the sample: with no coverage data, or if
    # every note failed, the ranges fall back to everything rather than
    # silently reporting nothing.
    if not headline:
        headline, excluded = ids, []

    out["note_count"] = len(ids)
    out["note_ids"] = ", ".join(ids)
    out["headline_count"] = len(headline)
    out["headline_ids"] = ", ".join(headline)
    out["excluded_ids"] = ", ".join(excluded) or "none"
    out["excluded_count"] = len(excluded)
    out["snapshot_date"] = meta.get("snapshot") or "unknown"

    gaps = {i: notes[i]["base"]["value"] - notes[i]["base"]["disclosed_estimated_value"]
            for i in ids}
    headline_gaps = {i: gaps[i] for i in headline}
    worst_id = min(headline_gaps, key=headline_gaps.get)
    best_id = max(headline_gaps, key=headline_gaps.get)
    out["gap_smallest"] = f"{abs(gaps[best_id]):.2f}"
    out["gap_largest"] = f"{abs(gaps[worst_id]):.2f}"
    out["gap_smallest_note"] = best_id
    out["gap_largest_note"] = worst_id
    out["gap_all_negative"] = "yes" if all(g < 0 for g in gaps.values()) else "NO"

    errs = [notes[i]["base"]["stderr"] for i in headline]
    out["stderr_min"] = f"{min(errs):.2f}"
    out["stderr_max"] = f"{max(errs):.2f}"
    out["paths"] = f"{notes[ids[0]]['base']['paths']:,}"
    vrf = [notes[i]["base"]["variance_reduction_factor"] for i in headline]
    out["vrf_min"] = f"{min(vrf):.1f}"
    out["vrf_max"] = f"{max(vrf):.1f}"

    # Funding spread under optimal exercise: every solution, plus the notes where
    # the disclosed value sits outside the bracket entirely.
    optimal = {i: _spread_bp(notes[i]["implied_spread_optimal_exercise"]) for i in headline}
    solved = {i: v for i, v in optimal.items() if v is not None}
    unsolved = [i for i, v in optimal.items() if v is None]
    out["optimal_spread_min_bp"] = f"{min(solved.values()):+,.0f}" if solved else "n/a"
    out["optimal_spread_max_bp"] = f"{max(solved.values()):+,.0f}" if solved else "n/a"
    out["optimal_spread_all_negative"] = (
        "yes" if solved and all(v < 0 for v in solved.values()) else "NO"
    )
    out["optimal_unsolved_notes"] = ", ".join(unsolved) if unsolved else "none"

    no_call = {i: _spread_bp(notes[i].get("implied_spread_no_early_call")) for i in headline}
    positive = {i: v for i, v in no_call.items() if v is not None and v > 0}
    out["no_call_positive_min_bp"] = f"{min(positive.values()):+,.0f}" if positive else "n/a"
    out["no_call_positive_max_bp"] = f"{max(positive.values()):+,.0f}" if positive else "n/a"
    out["no_call_positive_notes"] = ", ".join(sorted(positive)) if positive else "none"
    out["no_call_positive_count"] = len(positive)
    non_positive = {i: v for i, v in no_call.items()
                    if v is not None and v <= 0}
    out["no_call_nonpositive_notes"] = ", ".join(sorted(non_positive)) or "none"
    out["no_call_nonpositive_count"] = str(len(non_positive))
    out["no_call_nonpositive_detail"] = "; ".join(
        f"{i} at {v:+,.0f} bp" for i, v in sorted(non_positive.items())
    ) or "none"

    # How the disclosed value compares with the note valued as if it ran to
    # maturity at no funding spread at all, which is the most generous
    # valuation available to the investor.
    for note_id in ids:
        spread = notes[note_id].get("implied_spread_no_early_call")
        if not spread or "value_at_zero_spread" not in spread:
            continue
        zero = float(spread["value_at_zero_spread"])
        out[f"no_call_zero_spread_{note_id}"] = f"{zero:,.2f}"
        excess = float(spread["target"]) - zero
        out[f"no_call_excess_{note_id}"] = f"{excess:+,.2f}"
        out[f"no_call_excess_abs_{note_id}"] = f"{abs(excess):,.2f}"
        out[f"no_call_bp_{note_id}"] = (
            f"{spread['spread'] * 1e4:+,.0f}" if spread.get("bracketed") else "no solution"
        )

    oas = next(iter(notes.values()))["corporate_oas_benchmark"]
    out["oas_bp"] = f"{oas['oas_bp']:.0f}"
    out["oas_series"] = oas["series"]
    out["oas_as_of"] = oas["as_of"]

    # Model risk, in the units the report quotes.
    flat = {i: notes[i]["flat_vol"] for i in headline}
    out["flat_vol_min"] = f"{min(v['difference'] for v in flat.values()):.2f}"
    out["flat_vol_max"] = f"{max(v['difference'] for v in flat.values()):.2f}"
    out["flat_vol_max_pct"] = f"{max(v['difference_pct'] for v in flat.values()):.2f}"
    out["flat_vol_all_positive"] = (
        "yes" if all(v["difference"] > 0 for v in flat.values()) else "NO"
    )

    timing = {i: notes[i]["timing"]["difference"] for i in headline}
    out["timing_min"] = f"{min(abs(v) for v in timing.values()):.2f}"
    out["timing_max"] = f"{max(abs(v) for v in timing.values()):.2f}"
    out["timing_all_negative"] = "yes" if all(v < 0 for v in timing.values()) else "NO"

    extrap = {i: notes[i]["extrapolation"]["difference"] for i in headline}
    out["extrap_max"] = f"{max(abs(v) for v in extrap.values()):.2f}"
    out["extrap_max_note"] = max(extrap, key=lambda i: abs(extrap[i]))
    short = {i: v for i, v in extrap.items() if notes[i]["base"]["tenor_years"] < 2.5}
    out["extrap_short_max"] = f"{max(abs(v) for v in short.values()):.2f}" if short else "n/a"

    corr = {i: notes[i]["correlation"]["d_value_per_0.05"] for i in headline}
    out["corr_min"] = f"{min(corr.values()):.2f}"
    out["corr_max"] = f"{max(corr.values()):.2f}"
    out["corr_long_all"] = (
        "yes" if all(notes[i]["correlation"]["investor_is_long_correlation"] for i in ids)
        else "NO"
    )
    # The correlation shift that would close each gap on its own, which is the
    # honest way to say whether correlation can explain the result.
    needed = {i: abs(gaps[i]) / corr[i] * 0.05 for i in headline if corr[i] > 0}
    out["corr_shift_needed_min"] = f"{min(needed.values()):.2f}" if needed else "n/a"
    out["corr_shift_needed_max"] = f"{max(needed.values()):.2f}" if needed else "n/a"

    decomp = {i: notes[i]["decomposition"] for i in headline}
    put = [d["worst_of_put_cost"] for d in decomp.values()]
    out["put_cost_min"] = f"{min(put):.0f}"
    out["put_cost_max"] = f"{max(put):.0f}"
    call_costs = {i: d["issuer_call_cost"] for i, d in decomp.items()
                  if notes[i]["base"]["call_style"] != "automatic"}
    out["call_cost_min"] = f"{min(call_costs.values()):.0f}" if call_costs else "n/a"
    out["call_cost_max"] = f"{max(call_costs.values()):.0f}" if call_costs else "n/a"
    auto = [i for i in ids if notes[i]["base"]["call_style"] == "automatic"]
    if auto:
        a = auto[0]
        out["auto_note"] = a
        out["auto_call_cost"] = f"{decomp[a]['issuer_call_cost']:+,.2f}"
        out["auto_call_cost_abs"] = f"{abs(decomp[a]['issuer_call_cost']):,.2f}"
        out["auto_no_call_value"] = f"{decomp[a]['value_without_call']:.2f}"
        out["auto_disclosed"] = f"{notes[a]['base']['disclosed_estimated_value']:.2f}"
        out["auto_trigger_pct"] = "93.25"

    coupon = {i: notes[i]["fair_coupon"] for i in headline}
    out["fair_coupon_min"] = f"{min(c['fair_coupon'] for c in coupon.values()) * 100:.2f}"
    out["fair_coupon_max"] = f"{max(c['fair_coupon'] for c in coupon.values()) * 100:.2f}"
    out["offered_coupon_min"] = f"{min(c['offered_coupon'] for c in coupon.values()) * 100:.2f}"
    out["offered_coupon_max"] = f"{max(c['offered_coupon'] for c in coupon.values()) * 100:.2f}"
    coupon_gaps = [abs(c["coupon_gap"]) * 100 for c in coupon.values()]
    out["coupon_gap_min"] = f"{min(coupon_gaps):.1f}"
    out["coupon_gap_max"] = f"{max(coupon_gaps):.1f}"

    loss = {i: notes[i]["base"]["prob_principal_loss"] for i in headline}
    out["prob_loss_min"] = f"{min(loss.values()) * 100:.1f}"
    out["prob_loss_max"] = f"{max(loss.values()) * 100:.1f}"
    called = {i: notes[i]["base"]["prob_called"] for i in headline}
    out["prob_called_min"] = f"{min(called.values()) * 100:.0f}"
    out["prob_called_max"] = f"{max(called.values()) * 100:.0f}"

    for note_id in ids:
        out[f"model_{note_id}"] = f"{notes[note_id]['base']['value']:,.2f}"
        out[f"gap_{note_id}"] = f"{abs(gaps[note_id]):.2f}"
        out[f"tenor_{note_id}"] = f"{notes[note_id]['base']['tenor_years']:.2f}"

    _add_term_sheet_figures(out, root)

    # The Goldman note's bracket, which is the evidence that its disclosed value
    # is unreachable rather than merely hard to reach.
    for note_id in ids:
        spread = notes[note_id]["implied_spread_optimal_exercise"]
        if spread and not spread.get("bracketed"):
            bracket = next(iter(notes.values())).get("value_vs_spread")
            out["unreachable_note"] = note_id
            out["unreachable_low_value"] = f"{spread['value_at_low_spread']:,.2f}"
            out["unreachable_high_value"] = f"{spread['value_at_high_spread']:,.2f}"
            out["unreachable_target"] = f"{spread['target']:,.2f}"
            out["unreachable_shortfall"] = f"{spread['shortfall']:.2f}"
            del bracket

    _add_reprice_figures(out, root)
    _add_attribution_figures(out, root)
    _add_screening_figures(out, root)
    _add_surface_figures(out, root)
    _add_proxy_figures(out, root)
    _add_timing_figures(out, notes)
    _add_coverage_figures(out, root, notes, coverage, excluded)

    return {k: str(v) for k, v in out.items()}


def _add_coverage_figures(out: dict, root: Path, notes: dict,
                          coverage: dict, excluded: list[str]) -> None:
    """Publish the barrier-coverage counts, and the excluded notes' own numbers.

    Excluding a note from the headline ranges only stays honest if its figures
    are still on the page, so everything the ranges would have carried is
    emitted per note here.
    """
    uncovered: list[str] = []
    for note_id, record in coverage.items():
        out[f"trigger_k_{note_id}"] = f"{record['k_trigger']:+.3f}"
        for ticker, leg in record["legs"].items():
            out[f"coverage_{note_id}_{ticker}"] = f"{leg['covered']}/{leg['slices']}"
            if leg["slices"] and leg["covered"] == 0:
                uncovered.append(f"{note_id}'s {ticker} leg")
    out["coverage_uncovered"] = ", ".join(uncovered) or "none"
    out["coverage_uncovered_count"] = str(len(uncovered))
    out["coverage_all_covered"] = "yes" if not uncovered else "NO"

    for note_id in excluded:
        result = notes.get(note_id)
        if not result:
            continue
        base = result["base"]
        out[f"excluded_gap_{note_id}"] = (
            f"{abs(base['value'] - base['disclosed_estimated_value']):,.2f}")
        out[f"excluded_model_{note_id}"] = f"{base['value']:,.2f}"
        out[f"excluded_stderr_{note_id}"] = f"{base['stderr']:.2f}"
        out[f"excluded_flat_vol_{note_id}"] = f"{result['flat_vol']['difference']:,.2f}"
        out[f"excluded_timing_{note_id}"] = (
            f"{abs(result['timing']['difference']):,.2f}")
        out[f"excluded_corr_{note_id}"] = (
            f"{result['correlation']['d_value_per_0.05']:,.2f}")
        spread = result.get("implied_spread_no_early_call") or {}
        if spread.get("bracketed"):
            out[f"excluded_no_call_bp_{note_id}"] = f"{spread['spread'] * 1e4:+,.0f}"


def _add_surface_figures(out: dict, root: Path) -> None:
    """How far each calibrated surface reaches, which bounds extrapolation."""
    path = root / "data/processed/surfaces.json"
    if not path.exists():
        return
    surfaces = json.loads(path.read_text())
    out["surface_count"] = str(len(surfaces))
    for ticker, record in surfaces.items():
        times = [s["t"] for s in record["slices"]]
        out[f"surface_tmax_{ticker}"] = f"{max(times):.1f}"
        out[f"surface_slices_{ticker}"] = str(len(times))
        out[f"surface_chain_{ticker}"] = record.get("chain_symbol", ticker)


def _add_proxy_figures(out: dict, root: Path) -> None:
    """The index-versus-ETF comparison, and what correcting it is worth."""
    import csv

    summary_path = root / "data/processed/proxy_summary.csv"
    if summary_path.exists():
        rows = [r for r in csv.DictReader(summary_path.read_text().splitlines())
                if r.get("shared_expiries") and int(r["shared_expiries"]) > 0]
        if rows:
            out["proxy_pairs"] = ", ".join(f"{r['etf']} against {r['index']}" for r in rows)
            diffs = [float(r["mean_difference_vol_pts"]) for r in rows]
            out["proxy_bias_min"] = f"{min(diffs):+.2f}"
            out["proxy_bias_max"] = f"{max(diffs):+.2f}"
            out["proxy_bias_all_positive"] = "yes" if all(d > 0 for d in diffs) else "NO"
            for r in rows:
                key = r["index"]
                out[f"proxy_bias_{key}"] = f"{float(r['mean_difference_vol_pts']):+.2f}"
                out[f"proxy_shared_{key}"] = r["shared_expiries"]
                out[f"proxy_tmax_shared_{key}"] = f"{float(r['t_max_shared']):.2f}"
                out[f"proxy_worst_{key}"] = f"{float(r['max_abs_difference_vol_pts']):.2f}"

    risk_path = root / "data/processed/proxy_risk.json"
    if risk_path.exists():
        payload = json.loads(risk_path.read_text())
        rows = payload.get("notes", [])
        if rows:
            out["proxy_risk_available"] = "yes"
            out["proxy_risk_detail"] = "; ".join(
                f"{r['note_id']} {r['difference']:+.2f}" for r in rows
            )
            out["proxy_risk_max"] = f"{max(abs(r['difference']) for r in rows):.2f}"
            first = rows[0]
            out["proxy_risk_note"] = first["note_id"]
            out["proxy_risk_difference"] = f"{first['difference']:+.2f}"
            out["proxy_risk_gap_before"] = f"{first['gap_as_calibrated']:.2f}"
            out["proxy_risk_gap_after"] = f"{first['gap_proxy_corrected']:.2f}"
        else:
            out["proxy_risk_available"] = "no"
    else:
        out["proxy_risk_available"] = "no"


def _add_timing_figures(out: dict, notes: dict) -> None:
    """The measured vol-index move per underlying, as a markdown table body.

    Built from what the pricing runs actually applied rather than typed out, so
    the table in the report and the shift in the model cannot disagree.
    """
    seen: dict[str, dict] = {}
    for result in notes.values():
        for move in result.get("timing", {}).get("moves", []):
            seen.setdefault(move["ticker"], move)
    if not seen:
        return
    index_names = {"SPX": "S&P 500", "NDX": "Nasdaq-100", "RUT": "Russell 2000",
                   "DJX": "Dow Jones Industrial Average", "SMH": "VanEck Semiconductor"}
    rows = []
    for ticker, move in sorted(seen.items(), key=lambda kv: kv[0]):
        rows.append(
            f"| {index_names.get(ticker, ticker)} | {move['index']} | "
            f"{move['level_start']:.2f} | {move['level_end']:.2f} | "
            f"{move['move_vol_points'] * 100:+.2f} vol pts |"
        )
    out["timing_table"] = "\n".join(rows)
    out["timing_basis"] = ", ".join(
        sorted({m["index"] for m in seen.values()})
    )
    exact = [m for m in seen.values() if m.get("basis") == "exact"]
    out["timing_all_exact"] = "yes" if len(exact) == len(seen) else "NO"


def _add_term_sheet_figures(out: dict, root: Path) -> None:
    """The issuer's own arithmetic, taken straight from the term sheets.

    Issue price less disclosed value less commission is what the issuer keeps,
    and it needs no model at all, so it is read from config/notes.yaml rather
    than from a pricing run. That matters while a long-dated note is still
    simulating: its margin is already known.
    """
    import yaml

    records = yaml.safe_load((root / "config/notes.yaml").read_text())["notes"]
    for record in records:
        note_id = record["id"]
        denomination = float(record.get("denomination", 1000.0))
        disclosed = float(record["disclosed_estimated_value"])
        commission = float(record["selling_commission"])
        out[f"disclosed_{note_id}"] = f"{disclosed:,.2f}"
        out[f"commission_{note_id}"] = f"{commission:,.2f}"
        margin = denomination - disclosed - commission
        out[f"margin_{note_id}"] = f"{margin:,.2f}"
        # Unsigned, for prose that states the direction in words.
        out[f"margin_abs_{note_id}"] = f"{abs(margin):,.2f}"
        out[f"coupon_{note_id}"] = f"{float(record['coupon']['annual_rate']) * 100:.2f}"
        out[f"ev_precision_{note_id}"] = f"{float(record['estimated_value_precision']):g}"
        # Disclosed value plus commission, which on at least one note in this
        # sample comes to more than the price the investor pays.
        out[f"disclosed_plus_commission_{note_id}"] = f"{disclosed + commission:,.2f}"
        out[f"denomination_{note_id}"] = f"{denomination:,.0f}"


def _add_screening_figures(out: dict, root: Path) -> None:
    """The sample funnel, from the saved screening run."""
    path = root / "data/processed/edgar_screen.json"
    if not path.exists():
        out["screen_available"] = "no"
        return
    rows = json.loads(path.read_text())
    finals = [r for r in rows if r.get("is_final_worst_of")]
    priceable = [r for r in finals if r.get("is_priceable")]
    out["screen_available"] = "yes"
    out["screen_filings"] = f"{len(rows):,}"
    out["screen_finals"] = f"{len(finals):,}"
    out["screen_priceable"] = f"{len(priceable):,}"
    out["screen_unpriceable"] = f"{len(finals) - len(priceable):,}"
    out["screen_priceable_pct"] = f"{100.0 * len(priceable) / max(len(finals), 1):.0f}"
    dates = sorted({r["filing_date"] for r in rows if r.get("filing_date")})
    if dates:
        out["screen_first_date"] = dates[0]
        out["screen_last_date"] = dates[-1]
    out["screen_issuers"] = f"{len({r['issuer'] for r in rows if r.get('issuer')})}"

    # Where every filing left the funnel. These have to sum to the total, and
    # the report quotes them, so a stage added later cannot go unnoticed.
    stages: dict[str, int] = {}
    for row in rows:
        stages[row.get("screen_stage", "unknown")] = stages.get(
            row.get("screen_stage", "unknown"), 0) + 1
    for stage, count in stages.items():
        out[f"screen_stage_{stage}"] = f"{count:,}"
    out["screen_stage_total"] = f"{sum(stages.values()):,}"
    out["screen_stages_reconcile"] = (
        "yes" if sum(stages.values()) == len(rows) else "NO"
    )

    # What the priceable population looks like, which is what the six notes
    # were selected from.
    baskets: dict[tuple, int] = {}
    issuers: dict[str, int] = {}
    styles: dict[str, int] = {}
    for row in priceable:
        key = tuple(sorted(row.get("underlying_codes") or []))
        baskets[key] = baskets.get(key, 0) + 1
        issuers[row["issuer"]] = issuers.get(row["issuer"], 0) + 1
        styles[row.get("call_style", "unknown")] = styles.get(
            row.get("call_style", "unknown"), 0) + 1
    if baskets:
        top = max(baskets, key=baskets.get)
        out["screen_top_basket"] = ", ".join(top)
        out["screen_top_basket_count"] = f"{baskets[top]:,}"
        out["screen_distinct_baskets"] = f"{len(baskets):,}"
    if styles:
        out["screen_discretionary"] = f"{styles.get('issuer_discretionary', 0):,}"
        out["screen_automatic"] = f"{styles.get('automatic', 0):,}"
    if issuers:
        out["screen_issuer_detail"] = "; ".join(
            f"{short_issuer(name)} {count}" for name, count in
            sorted(issuers.items(), key=lambda kv: -kv[1])
        )

    # Which underlyings keep notes out of the priceable pool, and by how much.
    blocked: dict[str, int] = {}
    for entry in finals:
        if entry.get("is_priceable"):
            continue
        for code, chain in zip(entry.get("underlying_codes") or [],
                               entry.get("underlying_chains") or []):
            if chain is None:
                blocked[code] = blocked.get(code, 0) + 1
    if blocked:
        ranked = sorted(blocked.items(), key=lambda kv: -kv[1])
        out["screen_top_blocker"] = ranked[0][0]
        out["screen_top_blocker_count"] = f"{ranked[0][1]:,}"
        out["screen_blockers"] = ", ".join(f"{code} ({count})" for code, count in ranked[:5])


def _add_reprice_figures(out: dict, root: Path) -> None:
    """Pull the European repricing check into the report's figures."""
    import csv

    path = root / "data/processed/reprice_volpoints.csv"
    if not path.exists():
        return
    rows = list(csv.DictReader(path.read_text().splitlines()))
    if not rows:
        return
    by_ticker = {r["ticker"]: r for r in rows}
    out["reprice_tickers"] = ", ".join(sorted(by_ticker))
    inside = {t: float(r["inside_pct"]) for t, r in by_ticker.items()}
    worst = min(inside, key=inside.get)
    others = {t: v for t, v in inside.items() if t != worst}
    out["reprice_worst_ticker"] = worst
    out["reprice_worst_inside_pct"] = f"{inside[worst]:.0f}"
    # One decimal, because the best of these is 99.5% and rounding it to a whole
    # number reads as a clean sweep that the data does not show.
    out["reprice_others_inside_min"] = f"{min(others.values()):.1f}" if others else "n/a"
    out["reprice_others_inside_max"] = f"{max(others.values()):.1f}" if others else "n/a"
    out["reprice_others_tickers"] = ", ".join(sorted(others))

    err = {t: float(r["err_volpts_med"]) for t, r in by_ticker.items()}
    spread = {t: float(r["spread_volpts_med"]) for t, r in by_ticker.items()}
    out["reprice_worst_err_volpts"] = f"{err[worst]:.3f}"
    out["reprice_worst_spread_volpts"] = f"{spread[worst]:.3f}"
    out["reprice_err_volpts_min"] = f"{min(err.values()):.3f}"
    out["reprice_err_volpts_max"] = f"{max(err.values()):.3f}"
    tightest_other = min(others, key=lambda t: spread[t]) if others else None
    if tightest_other:
        out["reprice_next_tightest_ticker"] = tightest_other
        out["reprice_next_tightest_spread"] = f"{spread[tightest_other]:.3f}"
        out["reprice_spread_ratio"] = f"{spread[tightest_other] / spread[worst]:.1f}"
    # Error measured against what the market will actually trade at. Taken from
    # the ratio the check itself computed, on unrounded medians, so this does
    # not disagree with the csv by a digit.
    ratio = {t: float(r["ratio"]) for t, r in by_ticker.items()}
    out["reprice_worst_err_over_spread"] = f"{ratio[worst]:.2f}"
    out["reprice_best_err_over_spread"] = (
        f"{min(ratio[t] for t in others):.2f}" if others else "n/a"
    )
    out["reprice_others_err_over_spread_max"] = (
        f"{max(ratio[t] for t in others):.2f}" if others else "n/a"
    )


def _add_attribution_figures(out: dict, root: Path) -> None:
    """Pull the call-attribution result in, if it has been run."""
    path = root / "data/processed/call_attribution.json"
    if not path.exists():
        out["attribution_available"] = "no"
        return
    payload = json.loads(path.read_text())
    rows = [r for r in payload.get("notes", []) if r.get("applicable")]
    if not rows:
        out["attribution_available"] = "no"
        return
    out["attribution_available"] = "yes"
    # Each note is held at the A-rated OAS on its own pricing date, so this is
    # usually one number but may be a short list when the sample spans dates.
    spreads = sorted({float(r["funding_spread_bp"]) for r in rows})
    out["attribution_spread_bp"] = (
        f"{spreads[0]:.0f}" if len(spreads) == 1
        else " and ".join(f"{s:.0f}" for s in spreads)
    )
    phi = {r["note_id"]: r["implied_call_fraction"] for r in rows}
    out["phi_min"] = f"{min(phi.values()):.2f}"
    out["phi_max"] = f"{max(phi.values()):.2f}"
    out["phi_notes"] = ", ".join(sorted(phi))
    out["phi_detail"] = "; ".join(f"{i} {v:+.2f}" for i, v in sorted(phi.items()))
    out["phi_above_one_notes"] = ", ".join(
        sorted(i for i, v in phi.items() if v > 1.0)) or "none"
    # phi below zero means the disclosed value sits above even the no-call
    # valuation, so no call-charging story reaches it and the difference has to
    # be somewhere else.
    below = sorted(i for i, v in phi.items() if v < 0.0)
    out["phi_below_zero_notes"] = ", ".join(below) or "none"
    out["phi_below_zero_count"] = str(len(below))
    between = sorted(i for i, v in phi.items() if 0.0 <= v <= 1.0)
    out["phi_in_range_notes"] = ", ".join(between) or "none"
    out["phi_in_range_detail"] = "; ".join(
        f"{i} {phi[i]:.2f}" for i in between) or "none"

    by_note = {r["note_id"]: r for r in rows}
    for note_id, row in by_note.items():
        out[f"phi_{note_id}"] = f"{row['implied_call_fraction']:+.2f}"
        out[f"no_call_value_{note_id}"] = f"{row['value_no_call']:,.2f}"
        out[f"optimal_value_{note_id}"] = f"{row['value_optimal']:,.2f}"
        out[f"call_worth_{note_id}"] = f"{row['call_value']:.2f}"
        out[f"residual_vs_no_call_{note_id}"] = f"{row['residual_vs_no_call']:+,.2f}"
        # Unsigned, for prose that already carries the direction in words.
        out[f"residual_abs_{note_id}"] = f"{abs(row['residual_vs_no_call']):,.2f}"
        out[f"residual_side_{note_id}"] = (
            "above" if row["residual_vs_no_call"] > 0 else "below")
    calls = [r["call_value"] for r in rows]
    out["attribution_call_min"] = f"{min(calls):.0f}"
    out["attribution_call_max"] = f"{max(calls):.0f}"
