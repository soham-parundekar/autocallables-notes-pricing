"""Claim-evidence matrix: re-derive each headline claim and check where it is used.

scripts/audit_paper_numbers.py asks whether a figure exists anywhere in the
project. This asks something stricter: whether the specific claim made in a
specific sentence is supported by a specific re-derivation from the frozen
data. The two fail on different things and both are needed.

The difference is not academic. Changing the 69 basis point corporate benchmark
to 70 passes the number audit, because 70 is a coupon barrier elsewhere in the
sample. It also passed the first version of this script, because 69 appears
twice in the results section and checking for presence anywhere in the section
found the other one. Anchoring the value to the phrase that makes the claim is
what closes that gap, which is why the consequential entries below carry a
`context` pattern rather than relying on membership.

Each entry recomputes its value from the saved results, the configuration or
the screening output, independently of whatever the manuscript says, then
asserts the manuscript says it in the right place. Exit status is 1 on any
miss.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.results import barrier_coverage, figures, load_results

SECTIONS = ROOT / "paper/sections"
results, meta = load_results(ROOT)
figs = figures(results, meta, ROOT)
cfg = {n["id"]: n for n in yaml.safe_load((ROOT / "config/notes.yaml").read_text())["notes"]}
screen = json.loads((ROOT / "data/processed/edgar_screen.json").read_text())
cov = barrier_coverage(ROOT)

TEXT = {p.name: p.read_text() for p in SECTIONS.glob("*.tex")}
ALL = "\n".join(TEXT.values())

rows: list[tuple[str, str, str, bool, str]] = []


def check(claim: str, where: str, derived, note: str = "", context: str = "") -> None:
    """Assert the derived value appears in the named section (or anywhere).

    `context` is a regex containing {v}, which is substituted with each printed
    spelling of the derived value. Supplying it binds the number to the sentence
    that makes the claim, which is the difference between checking that a figure
    exists and checking that it is used where it belongs. Without it a benchmark
    quoted twice in one section can be wrong in one place and still pass.
    """
    if isinstance(derived, bool):
        rows.append((claim, where, str(derived), derived, note + " (property, not a literal)"))
        return
    shown = f"{derived:,.2f}" if isinstance(derived, float) else f"{derived:,}"
    hay = TEXT.get(where, ALL) if where.endswith(".tex") else ALL
    # The manuscript writes a minus as $-$ and a thousands comma as {,}, and
    # may round a stored value to fewer places than it carries.
    variants = {shown, shown.replace(",", "{,}"), shown.lstrip("-"),
                shown.replace(",", "{,}").lstrip("-")}
    if isinstance(derived, float):
        for places in (0, 1, 2, 3, 4):
            for v in (f"{derived:,.{places}f}", f"{abs(derived):,.{places}f}"):
                variants |= {v, v.replace(",", "{,}")}
    if context:
        # The manuscript hard-wraps, so a distinctive phrase usually spans a
        # line break; match any run of whitespace wherever the anchor has one.
        pattern = re.sub(r"\s+", r"\\s+", context)
        ok = any(re.search(pattern.replace("{v}", re.escape(v)), hay) for v in variants if v)
    else:
        ok = any(v in hay for v in variants if v)
    rows.append((claim, where, shown, ok, note))


# ---- gaps -----------------------------------------------------------------
gaps = {n: results[n]["base"]["value"] - cfg[n]["disclosed_estimated_value"] for n in results}
headline = [n for n in results if cov.get(n, {}).get("fully_covered", True)]
excluded = [n for n in results if n not in headline]

check("every gap is negative", "ALL", all(g < 0 for g in gaps.values()),
      "asserted as a property, not a figure")
check("headline smallest gap", "06_results.tex", abs(max(gaps[n] for n in headline)),
      context=r"runs from \\\${v} to")
check("headline largest gap", "06_results.tex", abs(min(gaps[n] for n in headline)))
check("excluded note gap", "06_results.tex", abs(gaps[excluded[0]]))
check("smallest gap pct", "06_results.tex",
      round(min(abs(gaps[n] / cfg[n]["disclosed_estimated_value"]) * 100 for n in headline), 2))
check("largest gap pct", "06_results.tex",
      round(max(abs(gaps[n] / cfg[n]["disclosed_estimated_value"]) * 100 for n in headline), 2))
check("min stderr", "06_results.tex", round(min(results[n]["base"]["stderr"] for n in results), 2))
check("max stderr", "06_results.tex", round(max(results[n]["base"]["stderr"] for n in results), 2))

# ---- spreads --------------------------------------------------------------
opt = {n: results[n].get("implied_spread_optimal_exercise") or {} for n in results}
solved = {n: s["spread"] * 1e4 for n, s in opt.items() if s.get("bracketed")}
check("all solved optimal spreads negative", "ALL", all(v < 0 for v in solved.values()))
check("most negative solved spread", "06_results.tex", round(min(solved.values())),
      context=r"negative, from \$-{v}\$ to")
check("least negative solved spread", "06_results.tex", round(max(solved.values())))
check("corporate OAS benchmark bp", "06_results.tex", int(float(figs["attribution_spread_bp"])),
      context=r"{v} basis points, the option-adjusted spread")
check("N4 crossing bp", "06_results.tex", int(figs["crossing_bp_N4"]),
      context=r"crossing at \$-{v}\$ basis points")
check("N4 crossing margin bp", "06_results.tex", int(figs["crossing_margin_bp_N4"]))

nocall = {n: results[n].get("implied_spread_no_early_call") or {} for n in results}
pos_nocall = sum(1 for s in nocall.values() if s.get("bracketed") and s["spread"] > 0)
check("notes turning positive without the call", "06_results.tex", pos_nocall,
      "text says three")

# ---- call attribution -----------------------------------------------------
attr = json.loads((ROOT / "data/processed/call_attribution.json").read_text())["notes"]
phis = {a["note_id"]: a["implied_call_fraction"] for a in attr if a.get("applicable")}
inside = sorted(round(v, 2) for v in phis.values() if 0 <= v <= 1)
outside = sorted(round(v, 2) for v in phis.values() if v < 0)
check("phi inside unit interval, count", "06_results.tex", len(inside), "text says three")
for v in inside:
    check(f"phi = {v}", "06_results.tex", v)
for v in outside:
    check(f"phi = {v}", "06_results.tex", v)

# ---- issuer arithmetic ----------------------------------------------------
resid = {n: 1000.0 - cfg[n]["disclosed_estimated_value"] - cfg[n]["selling_commission"]
         for n in cfg}
pos = [v for v in resid.values() if v > 0]
check("positive residual min", "06_results.tex", round(min(pos), 2))
check("positive residual max", "06_results.tex", round(max(pos), 2))
check("negative residual", "06_results.tex", round(min(resid.values()), 2))

# ---- decomposition --------------------------------------------------------
dec = {n: results[n]["decomposition"] for n in results}
call_costs = {n: d["issuer_call_cost"] for n, d in dec.items() if d["issuer_call_cost"] > 0}
check("issuer call cost min", "06_results.tex", round(min(call_costs.values()), 2))
check("issuer call cost max", "06_results.tex", round(max(call_costs.values()), 2))
check("auto-call benefit to holder", "06_results.tex",
      round(abs(min(d["issuer_call_cost"] for d in dec.values())), 2))
coupon_largest = sum(1 for d in dec.values() if d["coupon_strip"] > d["worst_of_put_cost"])
check("notes where coupon strip exceeds the put", "06_results.tex", coupon_largest,
      "text says five of six")

# ---- model risk -----------------------------------------------------------
flat = {n: results[n]["flat_vol"] for n in results}
check("flat vol min effect", "07_which_inputs.tex", round(min(f["difference"] for f in flat.values()), 2),
      context=r"raises all six notes, by \\\${v}")
check("flat vol max effect", "07_which_inputs.tex", round(max(f["difference"] for f in flat.values()), 2),
      context=r"to\s+\\\${v}\.")
over = {n: flat[n]["flat_vol_value"] - cfg[n]["disclosed_estimated_value"] for n in flat}
check("overshoot count", "07_which_inputs.tex", sum(1 for v in over.values() if v > 0),
      "text says five of six")
check("smallest overshoot", "07_which_inputs.tex", round(min(v for v in over.values() if v > 0), 2))
check("largest overshoot", "07_which_inputs.tex", round(max(over.values()), 2))
check("the one shortfall", "07_which_inputs.tex", round(abs(min(over.values())), 2))

tim = {n: results[n]["timing"]["difference"] for n in headline}
check("timing min magnitude", "07_which_inputs.tex", round(min(abs(v) for v in tim.values()), 2))
check("timing max magnitude", "07_which_inputs.tex", round(max(abs(v) for v in tim.values()), 2))

ext = {n: results[n]["extrapolation"]["difference"] for n in headline}
check("extrapolation max", "07_which_inputs.tex", round(max(ext.values()), 2))
check("extrapolation min", "07_which_inputs.tex", round(min(ext.values()), 2))

corr = {n: results[n]["correlation"]["d_value_per_0.05"] for n in results}
check("correlation sensitivity min", "07_which_inputs.tex", round(min(corr.values()), 2))
check("correlation sensitivity max", "07_which_inputs.tex", round(max(corr.values()), 2))
need = {n: abs(gaps[n]) / corr[n] * 0.05 for n in headline}
check("correlation shift needed min", "07_which_inputs.tex", round(min(need.values()), 2))
check("correlation shift needed max", "07_which_inputs.tex", round(max(need.values()), 2))

proxy = json.loads((ROOT / "data/processed/proxy_risk.json").read_text())["notes"][0]
check("proxy correction", "ALL", round(proxy["difference"], 2))
check("proxy stderr", "ALL", round(proxy["stderr"], 2))

# ---- census ---------------------------------------------------------------
stage = lambda s: sum(1 for r in screen if r["screen_stage"] == s)
check("total filings", "03_sample_and_data.tex", len(screen))
check("preliminary", "03_sample_and_data.tex", stage("preliminary"))
check("not worst-of plus no coupon", "03_sample_and_data.tex",
      stage("not_worst_of") + stage("no_contingent_coupon"))
check("no estimated value", "03_sample_and_data.tex", stage("no_estimated_value"))
check("single name", "03_sample_and_data.tex", stage("single_name_or_fund_underlyings"))
check("no listed options", "03_sample_and_data.tex", stage("underlying_without_listed_options"))
check("priceable", "03_sample_and_data.tex", stage("priceable"))
finals = [r for r in screen if r.get("is_final_worst_of")]
check("finals", "03_sample_and_data.tex", len(finals))
check("priceable pct of finals", "03_sample_and_data.tex",
      round(stage("priceable") / len(finals) * 100),
      context=r"{v} per cent of the qualifying market")
ndxt = sum(1 for r in screen if r["screen_stage"] == "underlying_without_listed_options"
           and "NDXT" in {c for c, ch in zip(r["underlying_codes"], r["underlying_chains"]) if ch is None})
check("NDXT filings", "03_sample_and_data.tex", ndxt)
check("issuers in the sample", "ALL", len({cfg[n]["issuer"] for n in cfg}),
      "text must say four, not the population's five")

# ---- validation -----------------------------------------------------------
surf = json.loads((ROOT / "data/processed/surfaces.json").read_text())
check("total fitted slices", "05_validation.tex", sum(s["audit"]["n_slices"] for s in surf.values()))
check("butterfly failures", "ALL", sum(s["audit"]["butterfly_failures"] for s in surf.values()))
check("calendar failures", "ALL", sum(s["audit"]["calendar_failures"] for s in surf.values()))
check("tightest butterfly min", "05_validation.tex",
      round(min(s["audit"]["butterfly_min"] for s in surf.values()), 4))
binding = sum(1 for s in surf.values() if s["audit"]["calendar_min_gap"] < 1e-5)
check("surfaces where calendar binds", "05_validation.tex", binding, "text says three of five")

import csv as _csv
vp = {r["ticker"]: r for r in _csv.DictReader((ROOT / "data/processed/reprice_volpoints.csv").open())}
others = {t: float(r["ratio"]) for t, r in vp.items() if t != "SPX"}
check("worst non-SPX ratio", "05_validation.tex", round(max(others.values()), 2))
check("SPX ratio", "05_validation.tex", round(float(vp["SPX"]["ratio"]), 2),
      context=r"returns a ratio of {v}")
check("SPX absolute error", "05_validation.tex", round(float(vp["SPX"]["err_volpts_med"]), 3))
check("SPX median spread", "05_validation.tex", round(float(vp["SPX"]["spread_volpts_med"]), 3))
next_tight = min(float(r["spread_volpts_med"]) for t, r in vp.items() if t != "SPX")
check("next tightest spread", "05_validation.tex", round(next_tight, 3))
check("spread ratio", "05_validation.tex", round(next_tight / float(vp["SPX"]["spread_volpts_med"]), 1))
check("SPX inside spread pct", "05_validation.tex", round(float(vp["SPX"]["inside_pct"]), 1))

# ---- coverage -------------------------------------------------------------
bad = [(n, t, v) for n, r in cov.items() for t, v in r["legs"].items() if v["covered"] == 0]
check("legs with zero coverage", "ALL", len(bad), "text says one leg of one note")
partial = [(n, t) for n, r in cov.items() for t, v in r["legs"].items()
           if 0 < v["covered"] < v["slices"]]
check("partially covered legs", "ALL", len(partial), "appendix claims none")

def main(verbose: bool = True) -> int:
    width = max(len(c) for c, *_ in rows)
    fails = [r for r in rows if not r[3]]
    if verbose:
        for claim, where, shown, ok, note in rows:
            print(f"{'ok ' if ok else 'MISS'} {claim:<{width}}  {shown:>12}  {where:<28} {note}")
        print(f"\n{len(rows)} claims checked, {len(fails)} not supported where claimed")
    return 1 if fails else 0


def failures() -> list[tuple]:
    return [r for r in rows if not r[3]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quiet", action="store_true")
    raise SystemExit(main(verbose=not parser.parse_args().quiet))
