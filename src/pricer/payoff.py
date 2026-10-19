"""Autocallable payoff valuation, including the issuer's call.

The six notes in this sample share a skeleton - contingent coupon on a barrier,
early redemption, principal at risk through a worst-of put - but differ in every
detail that matters, so the engine is built around the differences rather than
around one template:

  * coupons monthly or quarterly, on barriers from 65% to 75% of initial
  * early redemption either automatic on a 93.25% trigger or at the issuer's
    discretion
  * principal at risk observed either only at maturity (European) or on every
    trading day (American knock-in)
  * one note whose loss condition needs both a knock-in and a close below
    initial

The interesting part is the call. One note in the sample is genuinely
auto-callable: redemption is mechanical, so the payoff is a plain barrier test.
The other five are callable at the issuer's option, which is a Bermudan option
the issuer holds against the investor, and it has to be valued by optimal
stopping rather than by a rule. Morgan Stanley's supplements say so outright -
redemption happens "if and only if the output of a risk neutral valuation model
... indicates that redeeming on such date is economically rational for us" - so
for those notes optimal exercise is the disclosed contract term, not an
assumption about behaviour.

Longstaff-Schwartz is used for that, with one sign convention worth stating
plainly: the issuer calls when continuing is worth *more* than redeeming,
because a high continuation value is the issuer's liability, not its asset. So
the investor's value at each call date is the redemption amount whenever the
estimated continuation value exceeds it. Getting that comparison backwards would
turn the issuer's option into a gift to the investor and raise every price.

Paths are simulated once and reused. The exercise policy and the discounting
both depend on the funding spread, and Module E solves for that spread by
repricing the note perhaps twenty times, so price() takes an existing simulation
and only redoes the cheap part.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np

from .simulate import SimulationResult

DAYS_PER_YEAR = 365.0


@dataclass
class NoteTerms:
    """One note's economics, as read from config/notes.yaml."""

    note_id: str
    issuer: str
    cusip: str
    denomination: float
    pricing_date: date
    maturity_date: date
    disclosed_estimated_value: float
    selling_commission: float
    estimated_value_precision: float
    tickers: list[str]
    initial_levels: np.ndarray
    coupon_rate: float
    coupon_frequency: str
    coupon_amount: float
    coupon_barrier: float
    memory: bool
    call_style: str
    call_level: float | None
    call_first_index: int | None
    call_last_index: int | None
    downside_trigger: float
    downside_observation: str
    downside_payoff: str
    downside_strike: float
    knock_in_window: tuple[float, float] | None
    observation_times: np.ndarray
    payment_times: np.ndarray
    call_eligible: np.ndarray      # bool per observation index

    @property
    def n_observations(self) -> int:
        return self.observation_times.size


def _year_fraction(start: date, end: date) -> float:
    return (end - start).days / DAYS_PER_YEAR


def build_terms(record: dict, *, valuation_date: date) -> NoteTerms:
    """Translate a YAML note record into the arrays the pricer uses.

    Times are measured from the option snapshot date, not from the note's
    pricing date, because the surface is calibrated on the snapshot. For the
    five notes priced on 7 October against a 9 October surface that shortens
    every observation by two days; src/analysis/timing_risk.py measures what
    that convention is worth.
    """
    pricing = _as_date(record["pricing_date"])
    maturity = _as_date(record["maturity_date"])
    schedule = record["schedule"]
    obs = np.array([_year_fraction(valuation_date, _as_date(d)) for d in schedule["observation"]])
    pay = np.array([_year_fraction(valuation_date, _as_date(d)) for d in schedule["payment"]])

    coupon = record["coupon"]
    periods_per_year = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}[
        coupon["frequency"]
    ]
    amount = coupon.get("amount_per_period")
    if amount is None:
        amount = record["denomination"] * coupon["annual_rate"] / periods_per_year

    call = record["call"]
    eligible = np.zeros(obs.size, dtype=bool)
    if call["style"] == "automatic":
        first = call.get("first_eligible_index") or 0
        last = call.get("last_eligible_index")
        last = obs.size - 1 if last is None else last
        eligible[first : last + 1] = True
    elif call["style"] in ("issuer_discretionary", "rational_exercise"):
        if call.get("first_eligible_index") is not None:
            eligible[call["first_eligible_index"] : (call["last_eligible_index"] or obs.size - 1) + 1] = True
        else:
            # Morgan Stanley lists its redemption dates explicitly rather than
            # describing them as a slice of the payment schedule.
            redemption = {_as_date(d) for d in schedule.get("redemption", [])}
            pay_dates = [_as_date(d) for d in schedule["payment"]]
            eligible = np.array([d in redemption for d in pay_dates], dtype=bool)
    # The final date is maturity, never an early redemption.
    if eligible.size:
        eligible[-1] = False

    downside = record["downside"]
    window = downside.get("knock_in_window")
    if window:
        window = (
            _year_fraction(valuation_date, _as_date(window[0])),
            _year_fraction(valuation_date, _as_date(window[1])),
        )

    return NoteTerms(
        note_id=record["id"],
        issuer=record["issuer"],
        cusip=record["cusip"],
        denomination=float(record["denomination"]),
        pricing_date=pricing,
        maturity_date=maturity,
        disclosed_estimated_value=float(record["disclosed_estimated_value"]),
        selling_commission=float(record["selling_commission"]),
        estimated_value_precision=float(record.get("estimated_value_precision", 0.01)),
        tickers=[u["ticker"] for u in record["underlyings"]],
        initial_levels=np.array([float(u["initial_level"]) for u in record["underlyings"]]),
        coupon_rate=float(coupon["annual_rate"]),
        coupon_frequency=coupon["frequency"],
        coupon_amount=float(amount),
        coupon_barrier=float(coupon["barrier"]),
        memory=bool(coupon.get("memory", False)),
        call_style=call["style"],
        call_level=call.get("level"),
        call_first_index=call.get("first_eligible_index"),
        call_last_index=call.get("last_eligible_index"),
        downside_trigger=float(downside["trigger"]),
        downside_observation=downside["observation"],
        downside_payoff=downside["payoff"],
        downside_strike=float(downside.get("strike", 1.0)),
        knock_in_window=window,
        observation_times=obs,
        payment_times=pay,
        call_eligible=eligible,
    )


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def coupon_matrix(terms: NoteTerms, worst: np.ndarray) -> np.ndarray:
    """Coupon paid at each observation, before any call is applied.

    With a memory feature, an observation that clears the barrier also pays
    every coupon missed since the last one that cleared. None of the six notes
    in this sample has that feature, but the branch is implemented and unit
    tested because the product exists and the term sheets have to be read for
    it either way.
    """
    clears = worst >= terms.coupon_barrier
    if not terms.memory:
        return clears * terms.coupon_amount

    paid = np.zeros_like(clears, dtype=float)
    owed = np.zeros(worst.shape[0])
    for i in range(clears.shape[1]):
        owed += terms.coupon_amount
        paid[:, i] = np.where(clears[:, i], owed, 0.0)
        owed = np.where(clears[:, i], 0.0, owed)
    return paid


def terminal_payoff(terms: NoteTerms, sim: SimulationResult) -> np.ndarray:
    """Principal repayment at maturity, per path."""
    worst_final = sim.worst[:, -1].astype(float)
    par = terms.denomination

    if terms.downside_payoff == "worst_return_if_below_initial":
        # BofA: loss requires both a knock-in touch during the period and a
        # close below the starting level at valuation. Either one alone returns
        # principal, which is materially different from a plain barrier.
        loss = sim.knocked_in & (worst_final < terms.downside_strike)
        return np.where(loss, par * worst_final, par)

    # The others: principal unless the worst performer finishes below trigger,
    # in which case the investor takes the full decline one for one.
    breached = worst_final < terms.downside_trigger
    return np.where(breached, par * worst_final, par)


def _lsm_basis(worst: np.ndarray, best: np.ndarray, accrued: np.ndarray) -> np.ndarray:
    """Regression design for the continuation value.

    The worst performer is the state variable that drives everything, so it
    enters with a square to let the exercise boundary curve. The gap between
    best and worst carries the dispersion information that a worst-of note is
    actually exposed to, and accrued-but-unpaid coupon matters only when the
    note has a memory feature. Keeping the basis this small is deliberate: with
    a richer basis the regression starts fitting simulation noise and the
    estimated exercise boundary becomes worse, not better.
    """
    return np.column_stack([
        np.ones_like(worst), worst, worst**2, best - worst, accrued,
    ])


def price(
    terms: NoteTerms,
    sim: SimulationResult,
    discount_curve,
    *,
    funding_spread: float = 0.0,
    min_regression_paths: int = 500,
    controls: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict:
    """Value the note per unit of denomination, given simulated paths.

    `funding_spread` is added to the zero curve for discounting, and also enters
    the issuer's exercise decision, since the issuer values its own call off its
    own funding curve.
    """
    worst = sim.worst.astype(float)
    best = sim.best.astype(float)
    n_paths, n_obs = worst.shape

    pay_df = np.array([
        float(discount_curve.discount(t)[0]) * np.exp(-funding_spread * t)
        for t in terms.payment_times
    ])

    cashflow = coupon_matrix(terms, worst)
    cashflow[:, -1] += terminal_payoff(terms, sim)

    called_at = np.full(n_paths, -1, dtype=int)

    if terms.call_style == "automatic":
        # Mechanical: the first eligible observation at or above the trigger
        # redeems the note. argmax on a boolean array finds that first True.
        trigger = terms.call_level if terms.call_level is not None else 1.0
        hit = (worst >= trigger) & terms.call_eligible[None, :]
        any_hit = hit.any(axis=1)
        first = np.argmax(hit, axis=1)
        called_at = np.where(any_hit, first, -1)
        rows = np.flatnonzero(any_hit)
        if rows.size:
            idx = called_at[rows]
            cashflow[rows, idx] += terms.denomination
            # Everything after the redemption date is cancelled.
            after = np.arange(n_obs)[None, :] > idx[:, None]
            cashflow[np.ix_(rows, np.arange(n_obs))] = np.where(after, 0.0, cashflow[rows])

    elif terms.call_style in ("issuer_discretionary", "rational_exercise"):
        accrued = np.zeros(n_paths)
        # Backward induction. value_ahead is the pathwise value, discounted to
        # today, of every cashflow strictly after the date being considered.
        value_ahead = cashflow[:, -1] * pay_df[-1]
        exercised = np.zeros(n_paths, dtype=bool)
        exercise_index = np.full(n_paths, n_obs - 1)

        for i in range(n_obs - 2, -1, -1):
            if terms.call_eligible[i]:
                # The coupon due at i is paid whether or not the note is
                # redeemed, so it cancels out of the decision and the
                # comparison is against par alone. Including it on the
                # redemption side - which is what every term sheet's wording
                # invites, since they quote the redemption as par plus coupon -
                # makes calling look about one coupon more expensive than it is
                # and systematically under-calls.
                par_value = terms.denomination * pay_df[i]
                design = _lsm_basis(worst[:, i], best[:, i], accrued)
                if n_paths >= min_regression_paths:
                    coeffs, *_ = np.linalg.lstsq(design, value_ahead, rcond=None)
                    estimated = design @ coeffs
                else:
                    estimated = value_ahead

                # The issuer redeems when keeping the note alive costs it more
                # than repaying principal now.
                call_now = estimated > par_value
                exercised = np.where(call_now, True, exercised)
                exercise_index = np.where(call_now, i, exercise_index)
                value_ahead = np.where(call_now, par_value, value_ahead)

            value_ahead = value_ahead + cashflow[:, i] * pay_df[i]

        called_at = np.where(exercised, exercise_index, -1)
        present_value = value_ahead
        return _summarise(terms, present_value, called_at, worst, sim, n_obs,
                          funding_spread, controls)

    present_value = (cashflow * pay_df[None, :]).sum(axis=1)
    return _summarise(terms, present_value, called_at, worst, sim, n_obs,
                      funding_spread, controls)


def control_variate_adjust(
    present_value: np.ndarray,
    controls: np.ndarray,
    expected: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Correct the simulated value using controls whose true price is known.

    The controls are European puts on each underlying struck at its downside
    trigger, priced on the same paths. Their exact values come from the
    calibrated surface, and src/vol/reprice_check.py has already established
    that this engine reproduces those prices, so whatever error the simulation
    makes on the control is a measurement of its error on the note.

    The coefficients are estimated by regressing the note's pathwise value on
    the controls rather than fixed at one, which is the standard choice and
    cannot increase variance: with several controls the optimal vector is
    Cov(C,C)^-1 Cov(C,V). The estimator stays unbiased because the controls are
    recentred on their known means, not on their sample means.
    """
    n = present_value.size
    deviation = controls - controls.mean(axis=0, keepdims=True)
    covariance = deviation.T @ deviation / max(n - 1, 1)
    cross = deviation.T @ (present_value - present_value.mean()) / max(n - 1, 1)
    try:
        beta = np.linalg.solve(covariance + 1e-12 * np.eye(covariance.shape[0]), cross)
    except np.linalg.LinAlgError:
        return present_value, np.zeros(controls.shape[1])
    return present_value - (controls - expected[None, :]) @ beta, beta


def _summarise(
    terms: NoteTerms,
    present_value: np.ndarray,
    called_at: np.ndarray,
    worst: np.ndarray,
    sim: SimulationResult,
    n_obs: int,
    funding_spread: float,
    controls: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict:
    raw_value = float(present_value.mean())
    raw_stderr = (
        float(present_value.std(ddof=1) / np.sqrt(present_value.size))
        if present_value.size > 1 else 0.0
    )
    beta = np.zeros(0)
    if controls is not None and present_value.size > 1:
        present_value, beta = control_variate_adjust(present_value, controls[0], controls[1])
    value = float(present_value.mean())
    # ddof=1 is undefined on a single path, which the hand-worked unit tests use.
    stderr = (
        float(present_value.std(ddof=1) / np.sqrt(present_value.size))
        if present_value.size > 1 else 0.0
    )
    called = called_at >= 0
    final_worst = worst[:, -1]

    if terms.downside_payoff == "worst_return_if_below_initial":
        loss = sim.knocked_in & (final_worst < terms.downside_strike) & ~called
    else:
        loss = (final_worst < terms.downside_trigger) & ~called

    return {
        "note_id": terms.note_id,
        "value": value,
        "stderr": stderr,
        "value_no_control_variate": raw_value,
        "stderr_no_control_variate": raw_stderr,
        "variance_reduction_factor": (raw_stderr / stderr) ** 2 if stderr > 0 else 1.0,
        "control_beta": beta.tolist(),
        "funding_spread": funding_spread,
        "paths": int(present_value.size),
        "prob_called": float(called.mean()),
        "mean_call_index": float(called_at[called].mean()) if called.any() else float("nan"),
        "expected_life_years": float(
            np.where(called, terms.payment_times[np.clip(called_at, 0, n_obs - 1)],
                     terms.payment_times[-1]).mean()
        ),
        "prob_principal_loss": float(loss.mean()),
        "expected_loss_given_loss": float(
            terms.denomination * (1.0 - final_worst[loss]).mean()
        ) if loss.any() else 0.0,
        "prob_knocked_in": float(sim.knocked_in.mean()) if sim.knocked_in.any() else 0.0,
    }
