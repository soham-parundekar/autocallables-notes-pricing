<!--
Source for reports/report.md. The prose is edited here; the numbers are not.
Each doubled-brace name below is filled by scripts/make_report.py from the saved
results, and a name that does not resolve stops the build. If a figure belongs
in the text, add it to figures() in src/analysis/results.py and cite it here.
-->

# What does the issuer keep?

### Pricing worst-of autocallable notes against the estimated values their issuers disclose

**Option snapshot {{snapshot_date}}. Notes priced 5 and 7 October 2026.**

Every 424B2 pricing supplement for a structured note carries an estimated value
below the $1,000 issue price. It is the bank's own model output for the note it
is selling, and it is the only public window into how these instruments are
valued by the people who build them. This project prices {{note_count}} of those
notes independently, from free data, using an arbitrage-free volatility surface
and a local-volatility Monte Carlo engine, and asks how close an outside analyst
can get.

{{headline_count}} of the {{note_count}} carry the headline figures. The
exception is {{excluded_ids}}, and the reason is a property of the data rather
than of the note: one of its three underlyings has no quoted option at the
strike where the note's barrier sits, so that leg's downside is extrapolated
rather than observed. Section 5 measures it. {{excluded_ids}} is priced anyway
and every one of its numbers appears below and in `tables.md`; it is kept out of
the ranges because averaging an extrapolated barrier into them would present it
as a measurement on the same footing as the rest. The criterion is coverage, not
the answer it produces: it removes the widest gap in the sample, so the headline
range is narrower than the one over all {{note_count}}.

Per-note numbers are in [`tables.md`](tables.md), generated from
`data/processed/analysis.json` by `scripts/make_tables.py`. Figures are in
[`figures/`](figures/). The figures quoted in this text are filled from the same
results files by `scripts/make_report.py`, so nothing here is transcribed.

---

## 1. What the exercise found

**Every model value lands below the value its issuer disclosed**, from
${{gap_smallest}} per $1,000 on {{gap_smallest_note}} to ${{gap_largest}} on
{{gap_largest_note}}, and the shortfall widens with tenor.
({{excluded_ids}}, outside the headline set, is further below still at
${{excluded_gap_N2}}.) The direction is what makes this informative. A funding spread discounts the note's cashflows more
heavily, so it can only push a model value down. A model already below the
disclosed value cannot be reconciled by any funding assumption, and the
inversion confirms it: solved at face value, every note returns a negative
implied spread, between {{optimal_spread_min_bp}} and
{{optimal_spread_max_bp}} bp.

**For {{unreachable_note}} the obstruction is structural.** When the issuer's
call is exercised rationally, lowering the discount rate makes the note more
valuable to hold, so the issuer calls it sooner, which caps the value near par
at every spread. Across the whole search bracket the model runs from
${{unreachable_low_value}} down to ${{unreachable_high_value}} and never reaches
the disclosed ${{unreachable_target}}. That disclosed value is unreachable at
any funding rate if the call it describes is exercised rationally.

**Ignore the issuer's call and the picture becomes coherent for most of the
sample.** Priced as if it ran to maturity, {{no_call_positive_count}} of the
notes ({{no_call_positive_notes}}) imply a funding spread between
{{no_call_positive_min_bp}} and {{no_call_positive_max_bp}} bp. Those are
positive, plausible for bank funding at these tenors, and wider than the
{{oas_bp}} bp option-adjusted spread on A-rated corporate debt on the pricing
date, which is the ordering market convention expects: a note programme funds
dearer than the issuer's plain vanilla bonds.

The exception is the five-year note, and it is where the result gets
interesting. Even with the call removed and no funding spread applied at all,
which is the most generous valuation available to the investor, N1 comes to
${{no_call_zero_spread_N1}} against a disclosed ${{disclosed_N1}}: its disclosed
value is ${{no_call_excess_abs_N1}} from that ceiling, near enough to call it
exact. The other five-year note, {{excluded_ids}}, is further past it still
(${{no_call_zero_spread_N2}} against ${{disclosed_N2}},
${{no_call_excess_abs_N2}} above). So on the longest notes no funding assumption
and no call policy together reach the disclosed value.

**What the issuer keeps needs no model.** This is the issuer's own arithmetic,
so the data-coverage exclusion has no bearing on it and all {{note_count}} notes
count. Issue price less disclosed value less commission leaves ${{margin_N1}} on
the five-year JPMorgan note and ${{margin_N2}} on the near-identical Morgan
Stanley one. Those two are worth
putting side by side: same {{coupon_N1}}% coupon, same five-year tenor, same 70%
coupon barrier and 65% European trigger, same monthly Bermudan call from month
twelve. JPMorgan discloses ${{disclosed_N1}} and charges ${{commission_N1}} of
commission; Morgan Stanley discloses ${{disclosed_N2}} and charges
${{commission_N2}}. Two different splits between the disclosed-value gap and the
stated commission, landing within a dollar of each other. The Goldman note runs
the other way: ${{disclosed_N4}} disclosed plus a 0.525% underwriting discount
of ${{commission_N4}} sums to ${{disclosed_plus_commission_N4}}, more than the
investor pays.

**The skew is not a detail.** Repricing with a single at-the-money Black
volatility per underlying instead of the full local-vol surface raises the notes
by ${{flat_vol_min}} to ${{flat_vol_max}} per $1,000, up to
{{flat_vol_max_pct}}% of value. A structure whose entire downside sits 25 to 40%
below spot depends on the shape of the smile rather than its level, so a
flat-vol valuation of one of these is not approximately right.

---

## 2. The sample, and what a week of this market looks like

{{screen_filings}} 424B2 filings were retrieved from EDGAR for
{{screen_first_date}} to {{screen_last_date}}, across the
{{screen_issuers}} financing subsidiaries that issue this product: JPMorgan
Chase Financial Company LLC, GS Finance Corp., Morgan Stanley Finance LLC,
Citigroup Global Markets Holdings Inc., and BofA Finance LLC. Searching the
parent banks' CIKs instead finds almost nothing, since the notes are issued by
the financing subsidiary and guaranteed by the parent.

Every filing is accounted for, and the stages sum to the total:

| Stage | Count |
|---|---|
| 424B2 filings, {{screen_first_date}} to {{screen_last_date}} | {{screen_filings}} |
| Preliminary supplements, which carry a value range rather than a point | {{screen_stage_preliminary}} |
| Not a worst-of structure | {{screen_stage_not_worst_of}} |
| No contingent coupon | {{screen_stage_no_contingent_coupon}} |
| Worst-of, final, but no disclosed point value | {{screen_stage_no_estimated_value}} |
| Linked to single companies or sector funds rather than indices | {{screen_stage_single_name_or_fund_underlyings}} |
| Index-linked, but an underlying has no free listed option market | {{screen_stage_underlying_without_listed_options}} |
| **Priceable from free data** | **{{screen_priceable}}** |
| Selected for pricing | {{note_count}} |

So {{screen_finals}} filings in that week are final worst-of
contingent-coupon notes with a disclosed point value, and
{{screen_priceable}} of them, {{screen_priceable_pct}}%, can be priced from free
data. That is a larger share than the exercise's framing suggests, and the
reconciliation is the reason the number can be trusted. The issuers print a
registered mark inside their index names, and a pattern written for "S&P 500
Index" does not match "S&P 500 ®️ Index"; an earlier version of this screen
dropped every filing it could not parse that way and reported a far smaller
priceable count. A parsing bug of that shape reads exactly like a finding about
market opacity, so every stage above is counted and summed to the total, and
`tests/test_screen_reads_underlyings.py` pins the trademark handling.

What blocks the rest is concentrated. **{{screen_top_blocker}}** appears in
{{screen_top_blocker_count}} of the
{{screen_stage_underlying_without_listed_options}} index-linked notes whose
underlyings include something with no free listed option market, against a full
ranking of {{screen_blockers}}. It has no usable listed option market, and it is
popular in these structures precisely because it is volatile, which makes the
coupon look generous. A further
{{screen_stage_single_name_or_fund_underlyings}} notes are written on individual
companies or sector funds rather than indices, which is a large part of this
market and outside what this project prices.

Identifying underlyings by index name is what makes NDXT visible, and it is also
the step where the worst available mistake lives. "Nasdaq-100 Technology Sector
Index" contains the string "Nasdaq-100", so a loose name match reads NDXT as the
Nasdaq-100 and moves those {{screen_top_blocker_count}} notes into the priceable
pool to be priced against the wrong surface. The screen matches the most
specific name first and cuts each match out of the text before the next pattern
runs; `tests/test_screen_reads_underlyings.py` pins both that behaviour and the
trademark handling.

The population the six notes were drawn from is narrower than its size
suggests. {{screen_top_basket_count}} of the {{screen_priceable}} priceable
notes reference the same three indices ({{screen_top_basket}}), across
{{screen_distinct_baskets}} distinct baskets in all, and
{{screen_discretionary}} are callable at the issuer's option against
{{screen_automatic}} with an automatic trigger. By issuer:
{{screen_issuer_detail}}. The six priced here were chosen to span that
structural variation rather than to sample it randomly: four issuers, three call
mechanisms, tenors from 1.75 to 5 years, and both European and American
downside observation.

### The six notes

Full terms are in `config/notes.yaml`, each field checked against its filing by
`tests/test_term_sheets_match_filings.py`. The structural variety is why these
six were chosen:

| | Issuer | Underlyings | Tenor | Coupon | Coupon barrier | Early redemption | Principal at risk |
|---|---|---|---|---|---|---|---|
| N1 | JPMorgan | NDX, RUT, SPX | 5.0y | {{coupon_N1}}% monthly | 70% | issuer's option, monthly from month 12 | 65%, at maturity |
| N2 | Morgan Stanley | INDU, NDX, RUT | 5.0y | {{coupon_N2}}% monthly | 70% | rational-exercise rule, 48 dates | 65%, at maturity |
| N3 | Morgan Stanley | NDX, RUT, SPX | 3.0y | {{coupon_N3}}% monthly | 70% | rational-exercise rule, 30 dates | 60%, at maturity |
| N4 | Goldman Sachs | NDX, RUT, SPX | 1.9y | {{coupon_N4}}% monthly | 65% | issuer's option, monthly from Jan 2027 | 60%, at maturity |
| N5 | BofA | NDX, RUT, SPX | 1.75y | {{coupon_N5}}% quarterly | 75% | automatic at {{auto_trigger_pct}}% | 70%, any trading day |
| N6 | JPMorgan | RUT, SPX, SMH | 1.9y | {{coupon_N6}}% monthly | 65% | issuer's option, monthly | 60%, at maturity |

Two things in that table are not what the standard description of an
autocallable would lead you to expect.

**Only one of the six is genuinely auto-callable.** The other five are callable
at the issuer's option, which is a different instrument: the call is a Bermudan
option the issuer holds against the investor, and it has to be valued by optimal
stopping rather than by a barrier test. For the two Morgan Stanley notes this is
a disclosed contract term rather than an assumption about how a bank behaves.
The supplement says redemption occurs

> if and only if the output of a risk neutral valuation model on a business day,
> as selected by the calculation agent ... indicates that redeeming on such date
> is economically rational for us

and, in case of doubt, that "an early redemption of the securities will not
automatically occur based on the performance of the underliers." The issuer has
written the pricing model into the contract.

**The one note that is auto-callable differs in every other respect.** Its
trigger is {{auto_trigger_pct}}% of the starting level rather than the 100%
usually assumed, its coupons are quarterly, and its principal-at-risk feature is
a continuously observed American knock-in at 70% combined with a second
condition: the worst performer must also finish below its starting level. Either
condition alone returns principal. Reading it as a plain European barrier
overstates its loss probability substantially.

Initial levels agree across four independently read supplements where they
overlap. NDX 31,160.08 and RTY 2,793.204 appear identically in the JPMorgan,
Goldman and both Morgan Stanley notes priced on 7 October, which is the
strongest available check that the hand extraction is right.

---

## 3. The constraint that shapes everything

Free option data is a snapshot of the present. There is no free source of
historical option chains, so a note can only be priced honestly against a chain
that was already saved for its pricing date.

The chains here were captured for the session of **{{snapshot_date}}**: 105
symbols, every listed strike and expiry, from Cboe's delayed-quote feed, with
SHA-256 checksums. Five of the six notes priced on **7 October**, one on 5
October. That ordering is forced rather than careless. Rule 424(b)(2) gives the
issuer two business days to file the final supplement, and the estimated value
appears only in that final filing, so an analyst who learns a note's terms when
the final supplement appears can never have had its pricing-date option data.

Rather than leave the two-day gap as an unquantified caveat, it is measured.
Cboe publishes {{timing_basis}} daily, so the change in 30-day implied
volatility over 7 to 9 October 2026 is observable per underlying:

| Underlying | Index | 7 Oct | 9 Oct | Move |
|---|---|---|---|---|
{{timing_table}}

Every note is repriced with the surface shifted back by its measured move, and
the effect is reported next to its value. It runs against the headline finding,
costing ${{timing_min}} to ${{timing_max}} per $1,000 and widening the gap
rather than closing it, which is worth stating precisely because it is
inconvenient.

Two further conventions follow from the date gap. Valuation is as of each note's
own pricing date, with observation times measured from there and the Treasury
curve taken for that date, both of which are available. And the surface is
transferred by *moneyness*: each forward curve is rescaled onto the initial
level the filing discloses, so k = 0 means at-the-money for the note and the
fitted smile shape carries across unchanged. Spot moved under 1% on the indices
between the 7th and the 9th but 3.5% on SMH, so without that rescaling N6's
barriers would sit at the wrong point on its smile.

`src/data/option_snapshot.py` also runs forward. Pointed at a live session it
saves the chains needed to close the gap to zero for notes pricing that week,
which is how the exercise is designed to be run and how this sample could be
extended.

---

## 4. Method

Five stages, each validated before anything downstream relies on it.

### Quotes to implied volatilities

Mid prices, with zero-bid quotes, untradable spreads and strikes carrying
neither open interest nor volume discarded. The spread filter is two-sided: a
quote passes if its spread is inside 35% of its mid *or* inside 20 cents. A pure
percentage rule throws away every cheap wing quote, since a 0.10/0.20 market is
a 67% spread but a real one-tick market, and a pure absolute rule keeps far too
much near the money.

For each expiry the forward and discount factor are backed out together by
regressing the call-put spread on strike, since C − P = DF·(F − K) for European
options. This pins the forward and the implied dividend from traded prices, so
no dividend forecast is needed and no borrow assumption is smuggled in.
Residuals come in below 5e-4 of spot on every index.

ETF options are American, where that identity holds only approximately: the
early-exercise premium on in-the-money puts grows with strike and tilts the
regression. Fitted freely on SMH it returned a discount factor implying a 1.6%
zero rate against 4.3% Treasuries. For those names the discount factor is taken
from the Treasury curve and only the forward is fitted.

Implied vols are inverted with Jäckel's rational-guess method, which recovers
volatility to machine precision at forward prices below 1e-2. The wings are
where a worst-of note's barriers sit, and a Newton solver fails there silently.

### Implied volatilities to an arbitrage-free surface

Raw SVI per expiry in total implied variance, calibrated by the quasi-explicit
reduction: for a fixed (m, σ) the parameterisation is linear in its remaining
three parameters, so a two-dimensional outer search replaces a five-dimensional
one. This was adopted after the plain five-parameter fit converged to ρ = +0.39
on an SPX expiry whose skew is strongly negative, leaving 26 bp of error at the
money.

Three further details earned their place by fixing real failures:

*Settlement series are kept separate.* Cboe serves SPX (AM-settled) and SPXW
(PM-settled) under one symbol, with 3,458 strike-and-expiry collisions. Keying
slices on the expiry date alone merged two different instruments and produced 62
vol points of fit error with eighteen calendar violations.

*Expiries are thinned to monthly spacing.* Dupire's numerator is dw/dT, obtained
here by differencing adjacent fitted slices. SPX lists expiries three days
apart, and each slice carries roughly 20 bp of independent fit error, so
differencing two of them over dt = 0.008 years amplifies that noise about
125-fold. It showed up as 63% local volatility across the put wing at five weeks
on an index whose implied vols there are half that.

*The arbitrage conditions are enforced during the fit, across the band the
surface is later queried on.* Scoping them to each adjacent pair's quoted
overlap left total variance non-monotone everywhere else, which drove local
variance to zero over a fifth of the Dow surface.

### The surface to local volatility

Dupire in log-moneyness, with every derivative taken analytically from the
fitted slices rather than by differencing market quotes. The arbitrage
constraints are what make this work: calendar monotonicity keeps dw/dT
non-negative and the butterfly condition keeps the denominator positive, so
local variance is positive by construction. A local-vol surface full of NaNs is
nearly always an unenforced butterfly condition.

Beyond the longest listed expiry the surface is extrapolated by freezing the
smile and growing total variance linearly in maturity. An alternative that
carries the last observed variance slope forward is priced alongside, and the
spread between them is reported as the error bar rather than described as small.

### Local volatility to note values

Daily log-Euler steps on all underlyings jointly, each with its own drift from
its own forward curve, correlated through a factor of a correlation matrix
estimated from 252 daily log returns up to each note's own pricing date and
repaired to the nearest valid matrix when needed. Returns stop at the pricing
date rather than the snapshot date, so no future information enters a historical
valuation.

Paths are never stored: a five-year monthly note at {{paths}} paths on three
assets would be about six terabytes. The engine steps forward keeping only the
current level and records, at each observation, the two statistics every payoff
needs. The American knock-in is tested every step inside its window, which is
what "any trading day" requires.

The issuer's call is valued by Longstaff-Schwartz, with one sign convention
worth stating: the issuer redeems when continuing is worth *more* than repaying
par, because a high continuation value is its liability. A subtle version of
getting this wrong cost real accuracy here. The term sheets quote the redemption
amount as par plus the coupon then due, but that coupon is paid whether or not
the note is called, so it cancels from the decision. Including it on the
redemption side made calling look a coupon more expensive than it is and
systematically under-called.

Antithetic variates, plus European control variates struck at each note's
downside trigger and priced exactly off the calibrated surface. The controls cut
variance by a factor of {{vrf_min}} to {{vrf_max}}, which is what brings the
standard error on a five-year note down to ${{stderr_max}}.

### Note values to a funding spread

Brent inversion for the spread over Treasuries at which the model reproduces the
disclosed value, run under both call assumptions and compared against the ICE
BofA A-rated corporate index option-adjusted spread ({{oas_series}}),
{{oas_bp}} bp on {{oas_as_of}}. TRACE prints on each issuer's own bonds would be
better; TRACE's free interface is interactive and its API requires credentials,
which this project does not work around.

---

## 5. Validation

| Check | Result |
|---|---|
| Put-call parity residuals after forward and discount extraction | median below 5e-4 of spot on every index |
| Butterfly arbitrage across all five fitted surfaces | zero failures |
| Calendar arbitrage across all five fitted surfaces | zero failures |
| Treasury par-bond reprice from the bootstrapped curve | 100.000000 at every quoted tenor, worst error 3e-14 |
| Dupire and the simulation on a flat synthetic surface | local vol exact to 1e-10; Black prices within one standard error |
| Forward reproduction in the multi-asset simulation | within 0.6 standard errors at every maturity |
| Local-vol MC reprices listed European options, inside bid-ask | {{reprice_others_inside_min}}–{{reprice_others_inside_max}}% on {{reprice_others_tickers}}; **{{reprice_worst_inside_pct}}% on {{reprice_worst_ticker}}** |
| Simulated marginals against the surface-implied risk-neutral CDF | agree to about one standard error across five strike levels |
| Payoff engine against hand-computed scenarios | 10 scenarios pass, including the exercise sign and the double loss condition |
| Term-sheet config against the preserved filings | every scalar located in its own 424B2 |
| Cash-index vols against their ETF proxies, at shared expiries | ETF above index by {{proxy_bias_min}} to {{proxy_bias_max}} vol pts on {{proxy_pairs}} |
| Snapshot payloads against the checksums recorded when they were captured | every shipped chain matches |
| A fresh copy holding only the shipped files | recalibrating from the preserved chains reproduces all {{surface_count}} surfaces bit for bit, the suite passes, and `make report` regenerates this report byte for byte |

Three of these deserve more than a row.

**The {{reprice_worst_ticker}} repricing result is the one honest failure, and
it is a statement about that option market rather than about the surface.** Its
median repricing error is {{reprice_worst_err_volpts}} vol points, inside the
{{reprice_err_volpts_min}} to {{reprice_err_volpts_max}} range the five
underlyings span. It misses the inside-spread criterion because its median
quoted spread is {{reprice_worst_spread_volpts}} vol points, under eight basis
points of volatility and {{reprice_spread_ratio}} times tighter than the next
tightest market in the sample ({{reprice_next_tightest_ticker}}, at
{{reprice_next_tightest_spread}}). Measured as error over quoted spread,
{{reprice_worst_ticker}} sits at {{reprice_worst_err_over_spread}} against
{{reprice_best_err_over_spread}} to {{reprice_others_err_over_spread_max}} for
the others. The criterion is unreachable on the most liquid index options in the
world. A more flexible parameterisation would close it and give up the
arbitrage guarantees the local-vol step depends on, which is a bad trade, so the
honest statement is the one in the table plus the vol-point column in
`data/processed/reprice_volpoints.csv`.

**The ETF proxy carries a bias, and it is priced rather than waved through.**
The Dow leg of N2 has no usable option market of its own: after cleaning, the
snapshot leaves {{proxy_shared_DJX}} DJX expiry that DIA also lists, the
furthest DJX quote reaching 0.69 years against a note maturing in 2031. So the
Dow surface is fitted to DIA. Comparing at-the-money vols at every shared expiry
gives DIA above DJX by {{proxy_bias_DJX}} vol points, and the two control pairs
the project never needs show the same sign and a similar size:
{{proxy_bias_NDX}} for QQQ over NDX across {{proxy_shared_NDX}} shared expiries
and {{proxy_bias_RUT}} for IWM over RUT across {{proxy_shared_RUT}}. A
consistent gap in one direction across three independent pairs is a bias, not
sampling noise, and its likeliest source is that an ETF's American options and
its own borrow and dividend flow do not reduce to the cash index exactly.

Repricing {{proxy_risk_note}} with only the Dow leg shifted down by the measured
amount moves it by {{proxy_risk_difference}}, from a gap of
{{proxy_risk_gap_before}} to {{proxy_risk_gap_after}}. The direction is worth
noting because almost nothing else in this project runs that way: the investor
is short a down-and-in put, so an overstated vol makes the note too cheap, and
correcting it narrows the gap. It narrows it by a few dollars out of
${{gap_N2}}. The headline number keeps the uncorrected surface, since that is
the surface the pricer calibrated and the correction is an estimate of a bias
rather than a measurement of the Dow's own smile.

**The same leg has a second and larger problem, which the level bias hides.** A
worst-of note's entire downside sits at the trigger, so the surface is queried
at a log-moneyness of {{trigger_k_N2}} for {{proxy_risk_note}}. Counting the
fitted slices beyond one year that carry a quote at or below that point
separates interpolating the smile from extrapolating it, and the answer is
lopsided: every leg of every note in the sample is covered except one.
{{coverage_uncovered}} has {{coverage_N2_DJX}} slices covered, so for the whole
part of that note's life past the first year, its Dow barrier is priced off an
extrapolation rather than off a quote. The fit quality says the same thing from
the other side: DIA's worst slice carries 2.2 vol points of error against 0.1 or
better on the index surfaces, because beyond a year its surviving quotes span
barely ±0.2 in log-moneyness and five SVI parameters are being pinned by a
window far narrower than the one they are asked about.

That is why {{proxy_risk_note}}'s value carries a wider honest uncertainty than
its ${{excluded_stderr_N2}} standard error suggests, and it is the reason
{{excluded_ids}} is held out of every range in this report. The exclusion rule
is the coverage count above, applied to whatever fails it, so it would catch any
future note with the same problem and it catches nothing else here: the other
{{headline_count}} have a quote at the barrier on every leg.
The structural point is the one from Section 2, one level deeper: an underlying
can clear the screen by having listed options and still fail to have quotes
where a worst-of barrier actually sits.

**The synthetic test is what licenses trusting the rest.** On a flat surface the
Dupire local volatility comes back as exactly the input constant and the
simulation returns Black's prices, which pins the Dupire implementation, the
grid interpolation, the drift and the sampler at once. When the market
repricing first disagreed, that test is what established the fault lay in the
fitted surface rather than in the engine, and it located a genuine bug: the
carry from valuation to the first listed expiry had been taken from the slope
between the first two expiries instead of from spot, putting every simulated
forward 7 to 12 bp low at every maturity.

---

## 6. Results

### Model values against the disclosed values

See `tables.md`, Table 1. Every model value sits below its disclosed
counterpart, from ${{gap_smallest}} on {{gap_smallest_note}} to
${{gap_largest}} on {{gap_largest_note}}, and the gap widens with tenor.
Standard errors are ${{stderr_min}} to ${{stderr_max}} per $1,000 at
{{paths}} paths with control variates, so the gaps are two orders of magnitude
larger than the simulation noise.

### Why no funding spread explains it

Table 3. Solved against the base model, every note with a solution returns a
negative implied spread, between {{optimal_spread_min_bp}} and
{{optimal_spread_max_bp}} bp, and {{optimal_unsolved_notes}} returns no solution
at all. A negative funding spread would mean the issuer discounting its own
liability more lightly than Treasuries.

The {{unreachable_note}} case is worth following through, because it is a
statement about the instrument rather than about anyone's model. For an
issuer-callable note the value is not monotone in the discount rate the way a
bond's is. Lowering the rate raises the continuation value, which makes the
issuer call sooner, which caps what the investor can hold. Across the bracket
the model moves from ${{unreachable_low_value}} to
${{unreachable_high_value}} and never reaches ${{unreachable_target}}, missing
by ${{unreachable_shortfall}} at its best. Rational exercise caps the note's
value at approximately par, so a disclosed value a dollar below par is
incompatible with it at any funding rate.

Priced as if the call did not exist, {{no_call_positive_notes}} imply
{{no_call_positive_min_bp}} to {{no_call_positive_max_bp}} bp, which is
positive, plausible, and wider than the {{oas_bp}} bp A-rated corporate OAS.
{{no_call_nonpositive_detail}}, which says something sharper than a wide spread
would: on the longest note in the sample the disclosed value is already about
what the structure is worth with no call charged and no funding spread applied.

### How much of its own call does the disclosed value charge for?

Rather than report an impossible spread, `src/analysis/call_attribution.py`
holds the funding spread at the issuer's traded bond spread and asks how much of
the call the disclosed value is consistent with deducting:

    phi = [ V_no_call(s) − EV_disclosed ] / [ V_no_call(s) − V_optimal(s) ]

phi = 1 is a full charge for the rational value of the call; phi = 0 is none. At
s = {{attribution_spread_bp}} bp the call is worth ${{attribution_call_min}} to
${{attribution_call_max}} per $1,000 on the discretionary notes, and the results
are {{phi_detail}} (`data/processed/call_attribution.json`).

Those numbers split the sample into two cases, and the split is more
informative than any single figure.

**{{phi_in_range_notes}} land inside the interval**, at
{{phi_in_range_detail}}. N3's is the one to look at: its disclosed value of
${{disclosed_N3}} sits against a no-call valuation of
${{no_call_value_N3}} at the A-rated bond spread, ${{residual_abs_N3}} {{residual_side_N3}} it on a $1,000 note. Within simulation noise, Morgan
Stanley's disclosed value for that note is what the structure is worth if the
issuer's call is assumed never to be exercised and the funding spread is the
one its own bonds trade at. For {{unreachable_note}} and N6 the fraction is
{{phi_N4}} and {{phi_N6}}, so under a third of the call at most.

**{{phi_below_zero_count}} notes ({{phi_below_zero_notes}}) return a negative
fraction**, which is not a small charge but no charge at all: their disclosed
values sit above even the no-call valuation, by
${{residual_abs_N1}} and ${{residual_abs_N2}}. Both are the five-year notes,
and one of them ({{excluded_ids}}) is the note held out of the ranges, so N1
carries this finding on its own. No assumption about call policy reaches those numbers, because
removing the call entirely is the most generous assumption available to the
investor and it is still not enough.

So the gap is not one thing. On the shorter notes it is consistent with a
disclosed value that charges little or none of a call the issuer holds and will
exercise. On the five-year notes something else is also different, and the
candidates are the inputs an outside analyst cannot observe: correlation marked
above realised, a volatility surface shaped differently in the wings at five
years, or a funding curve that is not the Treasury curve plus a bond spread.
This exercise identifies the size of what must differ and where it cannot be;
it does not identify which.

The auto-callable note is excluded, instructively. Removing its automatic call
*lowers* the investor's value, to ${{auto_no_call_value}} against a disclosed
${{auto_disclosed}}, because redeeming at par in a good state protects the
holder from a later knock-in. An automatic trigger at {{auto_trigger_pct}}% is
worth something to the investor; a discretionary call is worth something to the
issuer. They are opposite instruments wearing similar names.

### What the issuer keeps, with no model involved

Table 2, and the one margin figure in this report that carries no modelling
assumption. It is the issuer's own arithmetic from its own filing.

The matched pair is the most informative row. N1 and N2 are the same economics
from two issuers, and the residual after the stated commission is ${{margin_N1}}
against ${{margin_N2}}. The other four run from ${{margin_N5}} to
${{margin_N3}}, while {{unreachable_note}} runs ${{margin_abs_N4}} the other way, because its
disclosed value plus its underwriting discount exceeds the issue price.

Goldman also quotes its estimated value to the nearest dollar where the others
report cents, so its disclosure carries a hundred times less precision on a
figure whose whole purpose is to tell the investor what the note is worth.

### The second lens: fair coupon

Table 3 in coupon terms. Solving for the coupon that would make each note worth
issue price less commission when discounted at the risk-free rate plus the
issuer's market credit spread gives {{fair_coupon_min}}% to {{fair_coupon_max}}%
against offered coupons of {{offered_coupon_min}}% to {{offered_coupon_max}}%.
The offered coupon runs {{coupon_gap_min}} to {{coupon_gap_max}} percentage
points below that, which is the same margin expressed in the unit an investor
shops on.

---

## 7. Risk, and the sign that has to be checked rather than reasoned

Table 4. Delta is reported per underlying because a worst-of note has no single
delta: exposure to one index depends on where the other two are.

**The investor is long correlation**, confirmed numerically at
${{corr_min}} to ${{corr_max}} per $1,000 for a +0.05 uniform shift. The
intuition runs opposite to first instinct, which is why it has to be checked:
lower correlation raises the probability that *at least one* underlying falls
through its barrier, which enriches the down-and-in put the investor is short,
while higher correlation makes the three move together so the worst performer is
less extreme. The measured loss probabilities move the same way, falling as
correlation rises.

That sign has an uncomfortable implication for this exercise. A dealer selling a
worst-of note is short correlation and marks it above realised; an outside
analyst has only realised correlation, since vanilla options carry none and no
free source of implied correlation exists. So this input is biased in a known
direction, and the Greek says what it is worth: closing the observed gaps
through correlation alone would need shifts of +{{corr_shift_needed_min}} to
+{{corr_shift_needed_max}}, which is not available when NDX and SPX already sit
at 0.93.

Vega is bucketed by maturity rather than quoted as one number, because a desk
hedging this note is short volatility in specific expiries, and the bucket
profile shows where.

### Model risk

Table 6.

**Smile risk is the large one.** Replacing the surface with a single
at-the-money Black volatility per underlying raises the notes by
${{flat_vol_min}} to ${{flat_vol_max}}, up to {{flat_vol_max_pct}}% of value. A
flat-vol valuation of one of these is not approximately right, which is the
answer the question deserves: the economics depend on the shape of the
volatility surface, not its level.

**Extrapolation risk is modest where it applies and concentrated in the
five-year notes.** SPX quotes reach {{surface_tmax_SPX}} years, but Nasdaq-100
quotes stop at {{surface_tmax_NDX}} and Russell 2000 at {{surface_tmax_RUT}},
against notes maturing in 2031. For the notes inside
2.5 years the effect is at most ${{extrap_short_max}}; the largest in the sample
is ${{extrap_max}} on {{extrap_max_note}}. The table reports it per note
alongside how far past the last listed expiry each underlying is pushed.

**Timing risk runs against the finding.** Shifting the surface back by the
measured VIX/VXN/RVX move costs ${{timing_min}} to ${{timing_max}} per $1,000,
widening the gap to the disclosed values rather than narrowing it.

### Economic decomposition

Table 5, split the way a desk would book it: a funding bond for the principal, a
strip of contingent coupons, a short worst-of down-and-in put, and the issuer's
call. The short worst-of put costs ${{put_cost_min}} to ${{put_cost_max}} per
$1,000 across the sample, and the issuer's call a further ${{call_cost_min}} to
${{call_cost_max}} on the discretionary notes. On {{auto_note}} that same line runs
${{auto_call_cost_abs}} the other way, because its automatic call protects the
holder instead of the issuer.

The pieces are reconciled against the note valued without its call, and the
residual is reported so that the interaction between the call and the coupon
strip is visible rather than quietly absorbed: calling stops future coupons as
well as capping the redemption.

Loss probabilities across the sample run {{prob_loss_min}}% to
{{prob_loss_max}}%, and the probability of being called at some point
{{prob_called_min}}% to {{prob_called_max}}%.

---

## 8. What would change the answer

Stated plainly, because the headline result is a gap this model cannot close
from free data alone.

**Pricing-date option chains.** The cleanest fix and the one the exercise is
designed for: run `src/data/option_snapshot.py` daily through a trading week,
select notes pricing Monday to Wednesday, and the surface date gap goes to zero.
The measured timing effect here is ${{timing_min}} to ${{timing_max}}, so this
removes a known contaminant rather than the gap.

**Implied correlation.** The single largest modelling approximation. Dealer runs
or a correlation swap market would replace a historical estimate with a
risk-neutral one, in the direction that narrows the gap. Quantified above: worth
${{corr_min}} to ${{corr_max}} per 0.05.

**A stochastic-volatility comparison.** Local volatility reproduces today's
surface exactly but gets the *forward* skew wrong, which matters for a
path-dependent product with a barrier and an early-redemption decision at every
observation. Calibrating Heston per underlying and repricing would separate "the
smile matters" from "local vol's particular smile dynamics matter". This is the
most valuable extension and it is not done here.

**TRACE prints.** Issuer-specific traded bond spreads rather than an index
bucket would sharpen the funding comparison, which currently rests on all four
issuers sitting in the A band.

---

## 9. Limitations

The surface is two trading days later than five of the six pricing dates, for
the structural reason in Section 3. Measured per note.

Correlation is realised, not implied, and biased in a known direction.

Discounting uses a Treasury curve where a dealer would use SOFR OIS. No free
source of OIS quotes exists. The two differ by a handful of basis points at
these tenors, small against funding spreads in the hundreds but not nothing.

The Dow surface is calibrated from DIA rather than DJX options, which is why
{{excluded_ids}} sits outside every range in this report. Two separate problems
come with that substitution and Section 5 measures both: DIA's at-the-money vols
sit {{proxy_bias_DJX}} vol points above DJX's, worth
{{proxy_risk_difference}} on {{proxy_risk_note}} when corrected, and more
seriously its long-dated quotes never reach the strike where the note's barrier
sits, so that leg's downside is extrapolated. {{excluded_ids}}'s own figures are
reported throughout and in `tables.md` — a model value of
${{excluded_model_N2}} against a disclosed ${{disclosed_N2}} — and should be
read as a case with wider uncertainty than its ${{excluded_stderr_N2}} standard
error implies.

Nothing rules out the same problem reaching a note that passes the coverage
test. The criterion asks whether a quote exists at the barrier, not whether the
quotes around it are good, and a thin market can clear it on one strike. The
other {{headline_count}} notes reference index options deep enough that this is
unlikely, but it is a threshold rather than a guarantee.

Local volatility is calibrated to European prices and reproduces them, but these
notes are path-dependent. Nothing here establishes that local vol prices the
path-dependence correctly, only that it prices the terminal distributions
correctly. The flat-vol comparison bounds how much the smile matters; it does
not bound how much the *dynamics* of the smile matter.

The issuer's call is priced under optimal exercise. Longstaff-Schwartz with a
finite basis gives a suboptimal policy, which biases the investor's value upward
relative to true optimal exercise, so it cannot explain a model that is already
too low.

Goldman discloses its estimated value to the nearest dollar, so comparisons
against {{unreachable_note}} carry $1 of irreducible disclosure precision
against a gap of ${{gap_N4}}.

Six notes, four issuers, one pricing week. Every result here is a
case study rather than an estimate of a population parameter. The
{{screen_finals}} final supplements identified for that week would support a
cross-sectional margin regression on tenor, coupon, barrier distance and
underlying count; that is left undone.

---

## 10. Reproducing this

```bash
make surfaces    # calibrate and cache the five SVI surfaces, with the arbitrage audit
make validate    # reprice listed European options off the local-vol engine
make analysis    # price the notes, invert spreads, Greeks, model risk
make test        # the test suite
make report      # regenerate tables.md, figures/ and this report
```

`make data` captures the *current* session's chains and is how the project is
pointed at a new pricing week. The {{snapshot_date}} snapshot these results rest
on is preserved with checksums under `data/raw/option_chains/{{snapshot_date}}/`,
and everything from `make surfaces` onward reproduces the numbers above from it.
Monte Carlo output is deterministic given the seed in `config/params.yaml`.

---

## References

Sources, with the component of the project that depends on each, are in
[`../references/references.md`](../references/references.md), with a structured
register in [`../references/source_register.csv`](../references/source_register.csv)
and BibTeX in [`../references/references.bib`](../references/references.bib). The
six 424B2 supplements are preserved in full under
[`../references/sec_filings/`](../references/sec_filings/).

The methodology rests on Gatheral and Jacquier for the SVI parameterisation and
its no-arbitrage conditions, Gatheral and Dupire for local volatility, Longstaff
and Schwartz for the issuer's call, Higham for correlation repair, Jäckel for
implied-volatility inversion, Glasserman for the variance reduction, and Martini
and Mingone for the quasi-explicit calibration reduction.
