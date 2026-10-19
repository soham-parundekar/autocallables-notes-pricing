# Pricing worst-of autocallable notes against their disclosed estimated values

Banks that sell autocallable structured notes have to publish, in every 424B2
pricing supplement, an estimated value below the $1,000 issue price. That number
is the bank's own model output for the note it is selling. This project prices
six of those notes independently, from free public data, on the dates they
actually priced, and asks how close an outside analyst can get.

The short answer is that the model lands **below** every disclosed value in the
sample, by $11 to $28 per $1,000 across the five notes that carry the headline
figures, and the sign of the gap is the interesting part. A funding spread can
only push a model value down, so no funding assumption explains a model that is
already too low.

The sixth note is held out of those ranges and reported separately, at $49
below its disclosed value. One of its three underlyings is the Dow, whose
option market is too thin to calibrate directly; the surface is fitted to DIA
instead, and DIA's long-dated quotes never reach the strike where that note's
barrier sits. Its downside is therefore extrapolated rather than observed. The
test is a coverage count applied to every note, and it catches nothing else
here: the other five have a quoted strike at their barrier on every leg.

Where the gap comes from splits the sample in two. On the shorter notes it is
consistent with a disclosed value that charges little or none of the call the
issuer holds: hold the funding spread at the issuer's own traded bond spread,
and the disclosed value of the three-year Morgan Stanley note lands within a
dollar of what the structure is worth if that call is assumed never to be
exercised. For the Goldman note the call is decisive in a sharper way, because
rational exercise caps the value near par, so its disclosed $999 cannot be
reproduced at **any** funding spread. On the two five-year notes something else
is different as well: removing the call entirely and charging no funding spread
at all is the most generous valuation available to the investor, and it still
does not reach what those issuers disclosed.

Full results, with the attribution and the caveats, are in
[`reports/report.md`](reports/report.md).

## The constraint that shapes the whole project

Free option data is a snapshot of now. There is no free source of historical
option chains, so a note can only be priced honestly if a full chain was already
saved for its pricing date. Everything here is organised around that.

The chains were captured on 2026-10-11 for the session of **2026-10-09**: 105
symbols, every listed strike and expiry, from Cboe's delayed-quote feed. The
notes in the sample priced on **2026-10-07** (five of six) and 2026-10-05, which
is as close as the published final supplements allow, because Rule 424(b)(2)
gives issuers two business days to file and the estimated value only appears in
that final filing. An outside analyst therefore cannot have pricing-date option
data for a note whose terms they only learn afterwards.

So the surface sits two trading days after the pricing date, and rather than
leave that as a caveat the gap is measured. Cboe publishes VIX, VXN and RVX
daily, so the two-day change in 30-day implied vol is observable per underlying:
over 7 to 9 October 2026 it was −0.24 vol points on the S&P 500, −0.45 on the
Nasdaq-100 and −1.08 on the Russell 2000. Every note is repriced with the
surface shifted by that measured move, and the difference is reported next to
its value. `src/data/option_snapshot.py` also runs forward: pointed at a current
session it saves the chains needed to close the gap to zero for notes pricing
that week.

## The notes

Six final 424B2 supplements, chosen to span the structures this market actually
issues rather than one template. All six are worst-of: the coupon and the
principal depend on the weakest of three underlyings, with no diversification
benefit.

| | Issuer | Underlyings | Priced | Tenor | Coupon | Coupon barrier | Early redemption | Principal at risk | Disclosed value |
|---|---|---|---|---|---|---|---|---|---|
| N1 | JPMorgan Chase Financial | NDX, RUT, SPX | 2026-10-07 | 5.0y | 8.30% monthly | 70% | issuer's option, monthly from month 12 | 65%, at maturity | $938.90 |
| N2 | Morgan Stanley Finance | INDU, NDX, RUT | 2026-10-07 | 5.0y | 8.30% monthly | 70% | rational-exercise rule, 48 dates | 65%, at maturity | $943.10 |
| N3 | Morgan Stanley Finance | NDX, RUT, SPX | 2026-10-07 | 3.0y | 9.75% monthly | 70% | rational-exercise rule, 30 dates | 60%, at maturity | $983.40 |
| N4 | GS Finance Corp. | NDX, RUT, SPX | 2026-10-07 | 1.9y | 10.15% monthly | 65% | issuer's option, monthly from Jan 2027 | 60%, at maturity | $999.00 |
| N5 | BofA Finance | NDX, RUT, SPX | 2026-10-05 | 1.75y | 6.25% quarterly | 75% | **automatic** at 93.25% | 70%, **any trading day** | $972.10 |
| N6 | JPMorgan Chase Financial | RUT, SPX, SMH | 2026-10-07 | 1.9y | 12.40% monthly | 65% | issuer's option, monthly | 60%, at maturity | $967.30 |

Two features of that table were surprises, and both change the pricing:

**Only one of the six is genuinely auto-callable.** The other five are callable
at the issuer's option, which makes the call a Bermudan option the issuer holds
against the investor rather than a barrier rule, and it has to be valued by
optimal stopping. For the two Morgan Stanley notes this is not an assumption
about behaviour but the disclosed contract term: redemption happens "if and only
if the output of a risk neutral valuation model ... indicates that redeeming on
such date is economically rational for us", and "will not automatically occur
based on the performance of the underliers."

**The one auto-callable note is the one that differs most.** Its trigger is
93.25% of the starting level rather than the 100% usually assumed, its coupons
are quarterly rather than monthly, and its principal-at-risk feature is a
continuously observed American knock-in paired with a second condition (the
worst performer must also close below its starting level), not a European test
at maturity. Reading it as a plain European barrier would materially overstate
its loss probability.

Initial levels agree across four independently read supplements where they
overlap, which is the cleanest available check on the extraction: NDX 31,160.08
and RTY 2,793.204 appear identically in the JPMorgan, Goldman and both Morgan
Stanley notes priced on 7 October.

## Method

Five stages, each validated before the next depends on it.

**Quotes to a surface.** Mid prices, with zero-bid and untradable quotes
dropped. For each expiry the forward and the discount factor come from
regressing the call-put spread on strike, so put-call parity pins the forward
and the implied dividend together from traded prices and no dividend forecast is
needed. Implied vols are inverted with Jäckel's method, which stays exact on the
wings where a Newton solver fails silently.

**Surface to an arbitrage-free fit.** Raw SVI per expiry in total implied
variance, calibrated by the quasi-explicit reduction: for fixed (m, σ) the
problem is linear in the other three parameters, so a two-dimensional search
replaces a five-dimensional one. Butterfly and calendar conditions are enforced
during the fit, not checked afterwards.

**Fit to local volatility.** Dupire in log-moneyness, with every derivative
taken analytically from the fitted slices. The arbitrage constraints are what
make this work: calendar monotonicity keeps dw/dT non-negative and the butterfly
condition keeps the denominator positive, so local variance is positive by
construction rather than by luck.

**Local vol to note values.** Daily log-Euler steps on all three underlyings at
once, correlated through a Cholesky factor of a correlation matrix estimated
from one year of daily returns up to each note's own pricing date and repaired
to the nearest valid matrix if needed. Antithetic variates and European control
variates struck at each note's downside trigger; the controls cut variance by a
factor of 1.8 to 3.9. Paths are never stored, since a five-year monthly note
would need about six terabytes at 200,000 paths.

**Values to a funding spread.** Brent inversion for the spread over Treasuries
at which the model reproduces the disclosed value, run under both call
assumptions, and compared against the ICE BofA A-rated corporate index
option-adjusted spread of 69 bp on the pricing date.

## What was validated, and what failed

| Check | Result |
|---|---|
| Put-call parity residuals after forward and discount extraction | median under 5e-4 of spot on every index |
| Butterfly arbitrage on the fitted surfaces | zero failures across all five underlyings |
| Calendar arbitrage on the fitted surfaces | zero failures across all five underlyings |
| Treasury curve bootstrap | every quoted par bond reprices to 100.000000, worst error 3e-14 |
| Dupire plus simulation on a flat synthetic surface | local vol exact to 1e-10; Black prices inside one standard error |
| Local-vol simulation reprices listed European options | 96–99% inside the quoted spread on four of five underlyings |
| | **SPX: 46%** — see below |
| Simulated marginals against the surface-implied risk-neutral CDF | agree within about one standard error |
| Payoff engine against hand-computed scenarios | 10 scenarios, including the issuer-call sign and the double loss condition |
| Term sheets against the preserved filings | every scalar in the config located in its 424B2 |

The SPX repricing result is the one honest failure, and it is worth reading
carefully rather than discounting. In vol points the SPX error is 0.131, which
is the second smallest of the five underlyings. SPX fails the inside-spread test
because its median quoted spread is **0.079 vol points** — under eight basis
points of volatility, roughly a twelfth of DJX's — and that is tighter than a
five-parameter-per-expiry surface can reproduce anywhere. The criterion is
unreachable on the most liquid index options in the world, not missed through a
defect, and a more flexible parameterisation would trade away the arbitrage
guarantees that make the local-vol step work at all.

## Reproducing this

Python 3.11 or later. `pip install -r requirements.txt`.

```bash
make data        # capture option chains plus the rates, index and spread series
make surfaces    # calibrate and cache the five SVI surfaces, with the arbitrage audit
make validate    # reprice listed European options off the local-vol engine
make analysis    # price the notes, invert spreads, Greeks, model risk
make test        # the full suite
make report      # tables, figures, and reports/report.md itself
```

`make data` captures **the current session**, not 2026-10-09. The snapshot this
project's results rest on is preserved under
`data/raw/option_chains/2026-10-09/` with SHA-256 checksums in its manifest, and
the rest of the pipeline reads from there, so `make surfaces` onwards reproduces
the published numbers exactly. Rerunning `make data` is how the project is
pointed at a new pricing week, not how these results are regenerated.

Monte Carlo output is deterministic for a given seed: path counts and the seed
live in `config/params.yaml`, and the Greeks re-seed identically so that
bump-and-revalue differences are sensitivities rather than noise.

## Layout

```
config/
  params.yaml       run configuration, with the reasoning behind each modelling choice
  notes.yaml        the six term sheets, hand-read and machine-verified against the filings
src/
  data/             chain snapshots, EDGAR screening, Treasury curve
  vol/              quote cleaning, forwards, SVI, Dupire, the repricing gate,
                    and the index-versus-ETF proxy comparison
  pricer/           correlation, simulation, payoff and the issuer's call
  analysis/         funding spread, Greeks, model risk, call attribution,
                    proxy risk, and the results runner
scripts/            tables, figures, the report renderer, the pre-publication scan
tests/              parity, no-arbitrage, local-vol repricing, hand-worked payoffs,
                    per-leg vol shifts, and the EDGAR screen's underlying parsing
references/         the six filings in full, a source register, and the bibliography
reports/            report_template.md holds the prose; report.md is generated
                    from it with every figure filled from the saved results
data/
  raw/              captured chains and series, never modified in place
  processed/        fitted surfaces and results, all regenerable
```

## Limitations worth knowing before using any number here

The surface is two trading days later than five of the six pricing dates. The
measured effect is reported per note.

Correlation is realised, not implied. No free source of implied correlation
exists, and vanilla options carry none. A dealer selling a worst-of note is
short correlation and marks it above realised, so this input is likely to be
conservative in a known direction, and the report quantifies what that is worth.

Discounting uses a Treasury curve where a dealer would use SOFR OIS, because
there is no free source of OIS quotes.

The five-year notes extrapolate beyond the longest listed expiry on two of their
three underlyings: Nasdaq-100 quotes reach 3.2 years and Russell 2000 quotes
2.2, against notes maturing in 2031. The base case freezes the smile and grows
total variance linearly in maturity; an alternative that carries the variance
slope forward is priced alongside, and the spread between them is the honest
error bar.

The Dow surface is calibrated from DIA rather than DJX options, because after
cleaning the snapshot leaves two usable DJX expiries, the furthest at 0.69
years, against a note running to 2031. That substitution carries a measured
bias rather than an assumed-away one: comparing at-the-money vols at every
shared expiry puts DIA about half a vol point above DJX, and the same sign and
size show up in the two control pairs the project never needs (QQQ over NDX,
IWM over RUT). Correcting only the Dow leg raises that note by about $2.60, so
the bias is real, quantified, and small against its gap. The comparison is
`src/vol/proxy_check.py`; the repricing is `src/analysis/proxy_risk.py`.

Bond spreads come from the ICE BofA A-rated corporate index rather than from
TRACE prints on each issuer's own bonds. TRACE's free interface is interactive
and its API needs credentials, which this project does not work around.
