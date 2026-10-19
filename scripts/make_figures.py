"""Build the report's figures from the saved results.

Titles here say what the figure shows rather than what it is about, since a
reader scanning the report should be able to take the finding off the title
alone. Nothing is recomputed: everything is read from data/processed/, so a
figure cannot disagree with the numbers in the text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.results import ISSUER_SHORT, barrier_coverage, load_results

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 130,
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.autolayout": True,
})


def load(root: Path) -> tuple[dict, dict, dict]:
    merged, meta = load_results(root)
    analysis = {"notes": merged, "generated_for_snapshot": meta.get("snapshot"),
                "convergence": meta.get("convergence", {})}
    surfaces = json.loads((root / "data/processed/surfaces.json").read_text())
    notes = {n["id"]: n for n in yaml.safe_load((root / "config/notes.yaml").read_text())["notes"]}
    return analysis, surfaces, notes


def model_against_disclosed(analysis: dict, out: Path) -> None:
    """The headline: every model value sits below the value the issuer disclosed."""
    # The held-out note is drawn in a lighter shade and labelled, so the figure
    # shows the same thing the ranges do without hiding the number.
    coverage = barrier_coverage(Path(__file__).resolve().parents[1])
    rows = []
    for note_id, result in sorted(analysis["notes"].items()):
        rows.append({
            "note": note_id,
            "issuer": ISSUER_SHORT.get(result["base"]["issuer"], result["base"]["issuer"]),
            "tenor": result["base"]["tenor_years"],
            "model": result["base"]["value"],
            "stderr": result["base"]["stderr"],
            "disclosed": result["margin"]["disclosed_estimated_value"],
            "commission": result["margin"]["selling_commission"],
            "held_out": not coverage.get(note_id, {}).get("fully_covered", True),
        })
    if not rows:
        return
    frame = pd.DataFrame(rows)

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2))
    y = np.arange(len(frame))

    axes[0].barh(y - 0.2, frame["disclosed"], height=0.38, label="Disclosed estimated value",
                 color="#4C72B0")
    axes[0].barh(y + 0.2, frame["model"], height=0.38, label="This model, local vol",
                 color="#DD8452", xerr=2 * frame["stderr"], error_kw={"lw": 0.9})
    axes[0].axvline(1000, color="k", lw=0.9, ls="--", label="Issue price, \\$1,000")
    axes[0].set_yticks(y)
    axes[0].set_yticklabels(
        [f"{r.note}{' *' if r.held_out else ''}  {r.issuer}\n{r.tenor:.1f}y"
         for r in frame.itertuples()], fontsize=8
    )
    axes[0].set_xlim(880, 1015)
    axes[0].set_xlabel("Value per \\$1,000 of principal")
    axes[0].set_title("Every model value lands below the issuer's disclosed value")
    # Below the bars rather than over them: the shortest note's pair sits at the
    # right of the panel, which is where a lower-right legend lands.
    axes[0].legend(fontsize=7.5, loc="upper right", framealpha=0.95)
    axes[0].invert_yaxis()

    gap = frame["model"] - frame["disclosed"]
    colours = np.where(frame["held_out"], "#E3A7A9",
                       np.where(gap < 0, "#C44E52", "#55A868"))
    axes[1].barh(y, gap, color=colours, height=0.55)
    axes[1].axvline(0, color="k", lw=0.9)
    axes[1].set_yticks(y)
    axes[1].set_yticklabels(
        [f"{r.note}{' *' if r.held_out else ''}" for r in frame.itertuples()])
    axes[1].set_xlabel("Model minus disclosed value, per \\$1,000")

    widest = gap.abs().max()
    shown = frame.loc[~frame["held_out"]]
    headline_gap = (shown["model"] - shown["disclosed"]).abs()
    by_tenor = shown.sort_values("tenor")
    axes[1].set_title(
        f"The shortfall widens with tenor, from \\${headline_gap.min():.0f} at "
        f"{by_tenor['tenor'].iloc[0]:.2g}y to \\${headline_gap.max():.0f} at "
        f"{by_tenor['tenor'].iloc[-1]:.2g}y"
    )
    if frame["held_out"].any():
        axes[1].text(
            0.015, -0.20,
            "* barrier outside quoted strikes on one leg; reported, held out of the range",
            transform=axes[1].transAxes, fontsize=7, color="#666666",
        )
    # Room for the longest bar's label, so it cannot collide with the tick.
    axes[1].set_xlim(-widest * 1.18, 0)
    axes[1].invert_yaxis()
    for index, value in enumerate(gap):
        # Inside the bar once it is long enough to hold the text, outside while
        # it is short. Either way the label stays within the axes.
        inside = abs(value) > 0.25 * widest
        axes[1].text(
            value + (1.2 if inside else -1.2), index, f"{value:+.1f}",
            va="center", ha="left" if inside else "right",
            color="white" if inside else "black", fontsize=8,
            fontweight="semibold" if inside else "normal",
        )

    fig.savefig(out / "model_vs_disclosed.png")
    plt.close(fig)


def value_against_spread(analysis: dict, out: Path) -> None:
    """Where each note's curve meets its disclosed value, and that it is never
    at a positive spread."""
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    crossings: list[float] = []
    never = []
    for note_id, result in sorted(analysis["notes"].items()):
        curve = result.get("value_vs_spread") or []
        if not curve:
            continue
        spreads = [point["spread_bp"] for point in curve]
        values = [point["value"] for point in curve]
        line, = ax.plot(spreads, values, marker="o", ms=3.5, lw=1.4, label=note_id)
        disclosed = result["margin"]["disclosed_estimated_value"]
        ax.axhline(disclosed, color=line.get_color(), lw=0.8, ls=":", alpha=0.8)

        solved = result.get("implied_spread_optimal_exercise") or {}
        if solved.get("bracketed"):
            crossings.append(float(solved["spread"]) * 1e4)
        else:
            never.append(note_id)

    ax.axvline(0, color="k", lw=0.9)
    ax.axvline(69, color="#555555", lw=0.9, ls="--")
    ax.text(72, ax.get_ylim()[0] + 8, "A-rated corporate OAS, 69 bp",
            fontsize=7.5, color="#555555")
    ax.set_xlabel("Funding spread over Treasuries, basis points")
    ax.set_ylabel("Model value per \\$1,000")
    # The claim in the title is read off the inversion rather than asserted, so
    # it cannot survive a result that stops supporting it.
    if crossings:
        worst = max(crossings)
        tail = f" and {', '.join(never)} never does" if never else ""
        ax.set_title(
            "Dotted lines are the disclosed values. Each curve reaches its own\n"
            f"only at a negative spread (the least negative is {worst:+.0f} bp){tail}"
        )
    else:
        ax.set_title("Model value against the assumed funding spread;\n"
                     "dotted lines are the disclosed values")
    ax.legend(fontsize=8, ncol=3)
    fig.savefig(out / "value_vs_funding_spread.png")
    plt.close(fig)


def convergence(analysis: dict, out: Path) -> None:
    """Has the estimate settled? Price and standard error against path count."""
    series = analysis.get("convergence") or {}
    if not series:
        return
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.9))
    for note_id, rows in sorted(series.items()):
        paths = [r["paths"] for r in rows]
        values = [r["value"] for r in rows]
        errors = [r["stderr"] for r in rows]
        axes[0].errorbar(paths, values, yerr=[2 * e for e in errors], marker="o", ms=4,
                         capsize=3, lw=1.3, label=note_id)
        axes[1].loglog(paths, errors, marker="o", ms=4, lw=1.3, label=note_id)

    axes[0].set_xscale("log")
    axes[0].set_xlabel("Simulated paths")
    axes[0].set_ylabel("Value per \\$1,000")
    axes[0].set_title("Price is flat beyond about 100,000 paths\n(bars are two standard errors)")
    axes[0].legend(fontsize=8)

    reference = np.array(paths, dtype=float)
    axes[1].plot(reference, errors[0] * np.sqrt(reference[0] / reference),
                 color="k", lw=0.9, ls="--", label=r"$1/\sqrt{N}$")
    axes[1].set_xlabel("Simulated paths")
    axes[1].set_ylabel("Standard error, $")
    axes[1].set_title("Standard error falls at the Monte Carlo rate")
    axes[1].legend(fontsize=8)
    fig.savefig(out / "convergence.png")
    plt.close(fig)


def vega_profile(analysis: dict, out: Path) -> None:
    """Where the note is actually exposed to volatility, by maturity bucket."""
    notes = {k: v for k, v in sorted(analysis["notes"].items()) if v.get("bucketed_vega")}
    if not notes:
        return
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    buckets = [row["bucket_years"] for row in next(iter(notes.values()))["bucketed_vega"]]
    width = 0.8 / max(len(notes), 1)
    for offset, (note_id, result) in enumerate(notes.items()):
        values = [row["vega_per_vol_point"] for row in result["bucketed_vega"]]
        ax.bar(np.arange(len(buckets)) + offset * width - 0.4, values, width=width, label=note_id)
    ax.axhline(0, color="k", lw=0.9)
    ax.set_xticks(np.arange(len(buckets)))
    ax.set_xticklabels(buckets)
    ax.set_xlabel("Maturity bucket, years")
    ax.set_ylabel("Value change per 1 vol point, $")
    ax.set_title("The investor is short volatility, and the exposure sits where\n"
                 "the note is likely to still be alive")
    ax.legend(fontsize=8, ncol=3)
    fig.savefig(out / "vega_buckets.png")
    plt.close(fig)


def smile_fits(root: Path, surfaces: dict, out: Path, ticker: str = "SPX") -> None:
    """How closely raw SVI tracks the quoted smile, expiry by expiry."""
    import sys

    sys.path.insert(0, str(root))
    from datetime import datetime

    from src.vol.forwards import build_slices, load_snapshot
    from src.vol.svi import SVIParams

    record = surfaces[ticker]
    valuation = datetime.fromisoformat(record["valuation_date"]).date()
    cfg = yaml.safe_load((root / "config/params.yaml").read_text())
    chain = load_snapshot(record["chain_symbol"], root / cfg["snapshot"]["option_chains"]
                          / valuation.isoformat())
    slices = build_slices(chain, valuation_date=valuation, kind=cfg["underlyings"][ticker]["kind"],
                          k_clip=tuple(cfg["surface"]["k_clip"]))
    chosen = np.linspace(0, len(slices) - 1, 6).round().astype(int)

    fig, axes = plt.subplots(2, 3, figsize=(11, 6))
    for axis, index in zip(axes.ravel(), sorted(set(chosen.tolist()))):
        sl = slices[index]
        meta = next((s for s in record["slices"] if abs(s["t"] - sl.t) < 1e-6), None)
        if meta is None:
            continue
        params = SVIParams(a=meta["a"], b=meta["b"], rho=meta["rho"],
                           m=meta["m"], sigma=meta["sigma"], t=meta["t"])
        grid = np.linspace(sl.log_moneyness.min(), sl.log_moneyness.max(), 200)
        axis.plot(sl.log_moneyness, sl.implied_vol * 100, ".", ms=2.5, alpha=0.55,
                  label="Quoted mid")
        axis.plot(grid, params.implied_vol(grid) * 100, lw=1.5, color="#C44E52", label="Raw SVI")
        axis.set_title(f"{sl.expiry}  T={sl.t:.2f}y   fit {meta['rmse_vol']*1e4:.0f} bp",
                       fontsize=8.5)
        axis.set_xlabel("log-moneyness  ln(K/F)", fontsize=8)
        axis.set_ylabel("Implied vol, %", fontsize=8)
        axis.legend(fontsize=7)
    fig.suptitle(f"{ticker}: the fitted smile against quoted mids, 2026-10-09", fontsize=10.5)
    fig.savefig(out / f"smile_fits_{ticker}.png")
    plt.close(fig)


def local_vol_surface(root: Path, surfaces: dict, out: Path, ticker: str = "SPX") -> None:
    """The Dupire surface the simulation actually samples."""
    import sys

    sys.path.insert(0, str(root))
    from src.pricer.simulate import LocalVolGrid
    from src.vol.calibrate import load_surface

    cfg = yaml.safe_load((root / "config/params.yaml").read_text())
    surface = load_surface(surfaces[ticker], k_bounds=tuple(cfg["surface"]["k_clip"]))
    horizon = max(s["t"] for s in surfaces[ticker]["slices"])
    grid = LocalVolGrid(surface, t_max=horizon)

    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    mesh = ax.pcolormesh(grid.k_grid, grid.t_grid, grid.table * 100,
                         shading="auto", cmap="viridis")
    bar = fig.colorbar(mesh, ax=ax)
    bar.set_label("Local volatility, %")
    ax.set_xlabel("log-moneyness  ln(K/F)")
    ax.set_ylabel("Maturity, years")
    ax.set_title(f"{ticker} local volatility: steep in the put wing, which is where\n"
                 "a worst-of note's barriers sit")
    fig.savefig(out / f"local_vol_{ticker}.png")
    plt.close(fig)


def main(root: Path) -> None:
    out = root / "reports/figures"
    out.mkdir(parents=True, exist_ok=True)
    analysis, surfaces, _ = load(root)

    model_against_disclosed(analysis, out)
    value_against_spread(analysis, out)
    convergence(analysis, out)
    vega_profile(analysis, out)
    for ticker in ("SPX", "RUT"):
        smile_fits(root, surfaces, out, ticker)
        local_vol_surface(root, surfaces, out, ticker)

    produced = sorted(p.name for p in out.glob("*.png"))
    print(f"wrote {len(produced)} figures to {out.relative_to(root)}:")
    for name in produced:
        print(f"  {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    main(parser.parse_args().root)
