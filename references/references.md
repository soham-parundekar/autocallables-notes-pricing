# References

Every source that materially shaped this project, with the component that
depends on it. Sources that were read during exploration but did not end up
informing the methodology, the data, or a claim in the report are deliberately
absent.

Metadata was verified against the originals rather than written from memory.
Where a detail could not be confirmed it is marked as unverified rather than
guessed, and no DOI, page range, or document number appears here that was not
read off the source.

## Methodology and literature

**REF-001 — Gatheral, J. and Jacquier, A., "Arbitrage-free SVI volatility surfaces."**
*Quantitative Finance*, 14(1), 59-71, 2014. DOI
10.1080/14697688.2013.819986. The implementation was written against the arXiv
version (arXiv:1204.0646v4, 21 March 2013); the journal volume and pages, which
this register previously recorded as unconfirmed, were confirmed against
Crossref on 2026-10-19, and the published version is the one cited.
Retrieved 2026-10-11 from https://arxiv.org/abs/1204.0646

Supplies the raw SVI parameterisation in total implied variance and, more
importantly, the two no-arbitrage conditions the project enforces: the function
g(k) whose non-negativity is equivalent to a non-negative risk-neutral density
(no butterfly arbitrage), and monotonicity of total variance in maturity (no
calendar arbitrage).

*Used by:* `src/vol/svi.py` (`butterfly_g`, `risk_neutral_density`, the
constrained refit, `audit`), `tests/test_svi_no_arbitrage.py`.

**REF-002 — Higham, N. J., "Computing the Nearest Correlation Matrix — A Problem from Finance."**
*IMA Journal of Numerical Analysis*, 22(3), 329–343, 2002. DOI
10.1093/imanum/22.3.329. Retrieved 2026-10-19 from
https://eprints.maths.manchester.ac.uk/232/

The alternating-projections method for the nearest valid correlation matrix in
the Frobenius norm. Needed in two places: a sample correlation matrix from
aligned daily returns can come back marginally non-positive-semidefinite, and a
uniform correlation bump for the risk scenarios frequently is.

*Used by:* `src/pricer/correlation.py` (`higham_nearest_correlation`,
`bump_correlation`), `tests/test_correlation_repair.py`.

**REF-003 — Longstaff, F. A. and Schwartz, E. S., "Valuing American Options by Simulation: A Simple Least-Squares Approach."**
*The Review of Financial Studies*, 14(1), 113–147, 2001. Metadata confirmed via
RePEc record https://ideas.repec.org/a/oup/rfinst/v14y2001i1p113-47.html

Least-squares Monte Carlo for optimal stopping. Five of the six notes are
callable at the issuer's option rather than on an automatic trigger, which makes
the call a Bermudan option the issuer holds against the investor; it cannot be
valued by a barrier rule. For the two Morgan Stanley notes this is not a
modelling convenience but the disclosed contract term, since the supplement
commits to redeeming "if and only if the output of a risk neutral valuation
model ... indicates that redeeming on such date is economically rational for us"
(REF-011, REF-012).

*Used by:* `src/pricer/payoff.py` (the backward induction in `price`,
`_lsm_basis`), `tests/test_payoff_hand_scenarios.py`.

**REF-004 — Gatheral, J., *The Volatility Surface: A Practitioner's Guide*.**
Wiley, 2006. ISBN not verified from the source and therefore not cited.

The source for Dupire's local volatility written in log-moneyness against total
implied variance, which is the form implemented here. Working in that
coordinate is what makes the derivatives available analytically from the fitted
SVI slices instead of by differencing market quotes.

*Used by:* `src/vol/local_vol.py` (`local_variance`).

**REF-005 — Dupire, B., "Pricing with a Smile."** *Risk*, 7(1), 18–20, January
1994. Page range not verified against the original; cited for the result rather
than for a quotation.

The original local-volatility result: a unique diffusion consistent with a full
surface of European option prices.

*Used by:* `src/vol/local_vol.py`.

**REF-006 — Jäckel, P., "Let's Be Rational."** *Wilmott Magazine*, 2015.
Implemented in the `py_vollib` / `py_lets_be_rational` package used here.

Implied volatility inversion that stays accurate across the whole moneyness
range. This matters in practice rather than in principle: a Newton solver
returns garbage or fails silently on deep out-of-the-money quotes, and the wings
are exactly where a worst-of note's barriers sit. Verified in this project to
recover vol to machine precision at forward prices down to 6.7e-3.

*Used by:* `src/vol/forwards.py` (`_implied_vol`), `src/pricer/engine.py`
(`build_controls`).

**REF-007 — Glasserman, P., *Monte Carlo Methods in Financial Engineering*.**
Springer, 2003.

Variance reduction: antithetic variates, and the control-variate estimator with
regression-estimated coefficients used here.

*Used by:* `src/pricer/simulate.py` (antithetic sampling),
`src/pricer/payoff.py` (`control_variate_adjust`).

**REF-008 — Martini, C. and Mingone, A., "No arbitrage SVI."** *SIAM Journal on
Financial Mathematics*, 13(1), 227-261, 2022. DOI 10.1137/20m1351060. The
preprint (arXiv:2005.03340, 2020) was what the implementation was written
against; the published version was located on Crossref on 2026-10-19 and
supersedes it, so the year to cite is 2022.
Retrieved 2026-10-19 from https://arxiv.org/pdf/2005.03340

Used for the quasi-explicit calibration reduction, and as the verifiable record
of its origin: this paper's introduction states that the 2009 whitepaper
*Quasi-explicit calibration of Gatheral's SVI* proposed the trick, and that it
also forms part of Stefano De Marco's PhD thesis. That whitepaper is commonly
attributed to Zeliade Systems; the attribution could not be confirmed from a
primary source, so it is recorded as unverified here. The substance is what the
project uses: for a fixed (m, sigma), raw SVI is linear in its remaining three
parameters, so the calibration reduces to a two-dimensional search around an
exact inner solve. Adopting it fixed a real failure — the unconstrained
five-parameter fit converged to rho = +0.39 on an SPX expiry whose skew is
strongly negative.

*Used by:* `src/vol/svi.py` (`_inner_linear_fit`, `fit_quasi_explicit`).

## SEC filings — the six priced notes

All six are final 424B2 pricing supplements, filed under each issuer's
structured-notes financing subsidiary rather than the parent bank. They are US
government public records retrieved from EDGAR and are preserved in full under
`references/sec_filings/`.

| Ref | Issuer | CUSIP | Accession | Priced | Local file |
|---|---|---|---|---|---|
| REF-010 | JPMorgan Chase Financial Company LLC | 46661PD38 | 0001918704-26-031125 | 2026-10-07 | `sec_filings/2026_JPMorganChaseFinancial_424B2_CallableContingentInterest_NDX-RTY-SPX_2031.htm` |
| REF-011 | Morgan Stanley Finance LLC | 61781LWY2 | 0001839882-26-050390 | 2026-10-07 | `sec_filings/2026_MorganStanleyFinance_424B2_CallableContingentIncome_INDU-NDX-RTY_2031.htm` |
| REF-012 | Morgan Stanley Finance LLC | 61781LM23 | 0001839882-26-050393 | 2026-10-07 | `sec_filings/2026_MorganStanleyFinance_424B2_CallableContingentIncome_NDX-RTY-SPX_2029.htm` |
| REF-013 | GS Finance Corp. | 40058M7F9 | 0001193125-26-419171 | 2026-10-07 | `sec_filings/2026_GSFinanceCorp_424B2_CallableContingentCoupon_NDX-RTY-SPX_2028.htm` |
| REF-014 | BofA Finance LLC | 09712CWN9 | 0001918704-26-030916 | 2026-10-05 | `sec_filings/2026_BofAFinance_424B2_ContingentIncomeAutoCallable_NDX-RTY-SPX_2028.htm` |
| REF-015 | JPMorgan Chase Financial Company LLC | 46661PL96 | 0001918704-26-031101 | 2026-10-07 | `sec_filings/2026_JPMorganChaseFinancial_424B2_CallableContingentInterest_RTY-SPX-SMH_2028.htm` |

Each supplies that note's underlyings and their initial levels, coupon rate and
frequency, coupon barrier, redemption terms, downside trigger and how it is
observed, maturity, selling commission, and the disclosed estimated value.
Overlapping initial levels agree across four of them, which is the project's
cleanest check on the extraction: NDX 31,160.08 and RTY 2,793.204 appear
identically in REF-010, REF-011, REF-012 and REF-013.

*Used by:* `config/notes.yaml`, `tests/test_term_sheets_match_filings.py`, and
every result in the report.

## Data sources

**REF-016 — Cboe delayed option quotes.**
`https://cdn-api.cboe.com/api/global/delayed_quotes/options/{symbol}.json`,
retrieved 2026-10-11 for the session of 2026-10-09.

Full option chains with bid, ask, both quote sizes and open interest per
contract. Cboe is the listing venue for SPX, NDX and RUT index options, so this
is the primary source rather than a redistribution. 105 symbols were captured;
the five the notes actually need are preserved under
`references/data_documentation/` with their SHA-256 manifests, and the full
capture lives in `data/raw/option_chains/2026-10-09/`.

Not redistributed beyond the five used underlyings: the full capture is 140 MB,
and Cboe's terms for the delayed feed do not clearly permit bulk
redistribution. `src/data/option_snapshot.py` reproduces any of it on demand,
but only for the current session — see the calendar constraint in the report.

**REF-017 — Cboe volatility index history (VIX, VXN, RVX).**
`https://cdn.cboe.com/api/global/us_indices/daily_prices/{VIX,VXN,RVX}_History.csv`,
retrieved 2026-10-11.

Daily 30-day implied volatility for the S&P 500, Nasdaq-100 and Russell 2000.
Used to measure, rather than assume, the two-day surface move between the notes'
pricing date and the only available option snapshot: over 7 to 9 October 2026
VIX fell 0.24 vol points, VXN 0.45 and RVX 1.08.

*Used by:* `src/analysis/model_risk.py` (`vol_index_move`, `timing_comparison`).

**REF-018 — US Treasury daily par yield curve, 2026.**
`https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all`,
retrieved 2026-10-11. US government public domain; preserved at
`data/raw/treasury_par_yields_2026.csv`.

Bootstrapped to continuously compounded zeros and used for all discounting, and
for the discount factor on the American ETF options where put-call parity
cannot be trusted. This is a Treasury curve standing in for the SOFR OIS curve a
dealer would use, because no free source of OIS swap quotes exists. The
substitution is stated in the report as a limitation rather than buried.

*Used by:* `src/data/rates.py`.

**REF-019 — FRED daily index levels: S&P 500, Nasdaq-100, Dow Jones Industrial Average.**
Series `SP500`, `NASDAQ100`, `DJIA` from
`https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}`, retrieved
2026-10-11.

One year of daily closes for correlation estimation. Levels reconcile exactly
with the Cboe snapshot on 2026-10-09 (SP500 7,811.54; NASDAQ100 30,883.150),
which cross-validates both sources.

*Used by:* `src/pricer/correlation.py`.

**REF-020 — Cboe Russell 2000 index history.**
`https://cdn.cboe.com/api/global/us_indices/daily_prices/RUT_History.csv`,
retrieved 2026-10-11. Used because FRED carries no Russell 2000 level series.
Reconciles with the option-chain close (2,806.9814 against 2,806.98).

*Used by:* `src/pricer/correlation.py`.

**REF-021 — Yahoo Finance daily closes for SMH.**
Retrieved 2026-10-11 through the `yfinance` package. Needed because neither FRED
nor Cboe publishes a daily series for an individual ETF. Reconciles with the
option-chain close (603.33). Not redistributed; `data/raw/yahoo_SMH_daily.csv`
is reproducible from the script.

*Used by:* `src/pricer/correlation.py`.

**REF-022 — ICE BofA A-rated US corporate index, option-adjusted spread.**
FRED series `BAMLC0A3CA`, retrieved 2026-10-19. 69 bp on 2026-10-07.

The benchmark the implied funding spreads are compared against. The brief asks
for FINRA TRACE prints on each issuer's own senior unsecured bonds, which would
be better. TRACE's free interface is interactive only and its API requires
credentials; the project does not bypass authentication, so this index is used
instead. All four issuers' senior unsecured ratings sit in the A band, making
the index the right bucket, but it is an index rather than any one issuer's
curve, and the report says so where the comparison is drawn.

*Used by:* `src/analysis/run_analysis.py` (`corporate_oas`),
`src/analysis/funding_spread.py` (`fair_coupon`).

**REF-023 — SEC EDGAR full-text search and submissions API.**
`https://efts.sec.gov/LATEST/search-index` and
`https://data.sec.gov/submissions/CIK{cik}.json`, accessed 2026-10-11.

How the note population was found. 597 424B2 filings were retrieved across five
financing subsidiaries for 5–9 October 2026, screened down to 56 final
supplements for worst-of contingent-coupon notes with a disclosed point estimate
of value, and then to the 15 whose underlyings have free listed option markets.
The CIKs used are JPMorgan Chase Financial Company LLC 0001665650, GS Finance
Corp. 0001419828, Morgan Stanley Finance LLC 0001666268, Citigroup Global
Markets Holdings Inc. 0000200245, and BofA Finance LLC 0001682472.

*Used by:* `src/data/edgar_notes.py`; the population counts quoted in the
report's sample-selection section.

## Sources deliberately not included

The brief lists Fang and Oosterlee (2008) on the Fourier-cosine method as
background for a Heston comparison. That extension was not built, so the paper
informs nothing here and is left out rather than padding the bibliography.
