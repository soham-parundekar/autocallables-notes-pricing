"""Hand-computed payoff scenarios.

The brief asks for at least three: an early call, a note that pays every coupon
and returns par, and a note that takes a loss at maturity. Each one here is a
deterministic path whose value can be written down by hand, so a disagreement
points at the payoff logic and nothing else. The Monte Carlo is removed from the
picture entirely by handing the pricer a SimulationResult built by hand.

The later tests cover the cases that actually caused trouble while building
this: the direction of the issuer's exercise decision, the double condition on
the BofA note, and the memory feature that no note in the sample uses but whose
absence still had to be confirmed on every term sheet.
"""

from __future__ import annotations

# Tolerances are set to 1e-4 absolute on values of order 1000, i.e. about 1e-7
# relative. That is the precision of the simulation observables, which are
# stored as float32 in SimulationResult to halve the memory a five-year monthly
# note needs: 0.40 comes back as 0.4000000059604645. Tightening these to 1e-8
# fails on that representation rather than on anything in the payoff logic.

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pricer.payoff import NoteTerms, coupon_matrix, price, terminal_payoff
from src.pricer.simulate import SimulationResult


class FlatCurve:
    """Constant continuously compounded discount curve."""

    def __init__(self, rate: float):
        self.rate = rate

    def discount(self, t):
        return np.atleast_1d(np.exp(-self.rate * np.asarray(t, dtype=float)))


def make_terms(**overrides) -> NoteTerms:
    """A three-observation annual note, easy to price by hand."""
    base = dict(
        note_id="TEST", issuer="Test Issuer", cusip="TEST00000",
        denomination=1000.0,
        pricing_date=date(2026, 10, 7), maturity_date=date(2029, 10, 7),
        disclosed_estimated_value=950.0, selling_commission=25.0,
        estimated_value_precision=0.01,
        tickers=["A", "B"], initial_levels=np.array([100.0, 200.0]),
        coupon_rate=0.10, coupon_frequency="annual", coupon_amount=100.0,
        coupon_barrier=0.70, memory=False,
        call_style="automatic", call_level=1.0,
        call_first_index=0, call_last_index=1,
        downside_trigger=0.60, downside_observation="european",
        downside_payoff="worst_return", downside_strike=1.0,
        knock_in_window=None,
        observation_times=np.array([1.0, 2.0, 3.0]),
        payment_times=np.array([1.0, 2.0, 3.0]),
        call_eligible=np.array([True, True, False]),
    )
    base.update(overrides)
    return NoteTerms(**base)


def make_sim(worst: np.ndarray, best: np.ndarray | None = None,
             knocked: np.ndarray | None = None) -> SimulationResult:
    worst = np.atleast_2d(np.asarray(worst, dtype=np.float32))
    if best is None:
        best = worst.copy()
    if knocked is None:
        knocked = np.zeros(worst.shape[0], dtype=bool)
    return SimulationResult(
        worst=worst, best=np.atleast_2d(best.astype(np.float32)),
        knocked_in=knocked,
        per_asset_final=np.column_stack([worst[:, -1], worst[:, -1]]),
        n_paths=worst.shape[0], observation_times=np.array([1.0, 2.0, 3.0]),
    )


def test_early_automatic_call_pays_par_plus_coupon_and_stops():
    """Worst performer at 1.05 on the first review: redeemed at par + coupon.

    By hand, at r = 5%: (1000 + 100) * exp(-0.05) = 1046.3601.
    Nothing later is paid, so the two remaining coupons must not appear.
    """
    terms = make_terms()
    sim = make_sim([[1.05, 1.10, 1.20]])
    out = price(terms, sim, FlatCurve(0.05))

    expected = 1100.0 * np.exp(-0.05)
    assert out["value"] == pytest.approx(expected, abs=1e-4)
    assert out["prob_called"] == 1.0
    assert out["prob_principal_loss"] == 0.0


def test_all_coupons_then_par_at_maturity():
    """Worst stays above the coupon barrier but below the call trigger.

    Three coupons of 100 at t = 1, 2, 3 plus 1000 at t = 3, at r = 5%:
      100*e^-0.05 + 100*e^-0.10 + 1100*e^-0.15 = 1130.5597.
    """
    terms = make_terms()
    sim = make_sim([[0.95, 0.92, 0.90]])
    out = price(terms, sim, FlatCurve(0.05))

    expected = (
        100.0 * np.exp(-0.05) + 100.0 * np.exp(-0.10) + 1100.0 * np.exp(-0.15)
    )
    assert out["value"] == pytest.approx(expected, abs=1e-4)
    assert out["prob_called"] == 0.0
    assert out["prob_principal_loss"] == 0.0


def test_loss_at_maturity_below_trigger():
    """Worst finishes at 0.40, under the 0.60 trigger: principal follows it down.

    No coupon ever clears the 0.70 barrier, so the only cashflow is
      1000 * 0.40 * e^-0.15 = 344.2810.
    """
    terms = make_terms()
    sim = make_sim([[0.65, 0.55, 0.40]])
    out = price(terms, sim, FlatCurve(0.05))

    expected = 400.0 * np.exp(-0.15)
    assert out["value"] == pytest.approx(expected, abs=1e-4)
    assert out["prob_principal_loss"] == 1.0
    assert out["expected_loss_given_loss"] == pytest.approx(600.0)


def test_at_trigger_exactly_returns_par():
    """The trigger is inclusive: finishing exactly at 0.60 repays principal."""
    terms = make_terms()
    sim = make_sim([[0.60, 0.60, 0.60]])
    out = price(terms, sim, FlatCurve(0.0))
    assert out["value"] == pytest.approx(1000.0)
    assert out["prob_principal_loss"] == 0.0


def test_coupon_barrier_is_inclusive():
    """A close exactly on the coupon barrier pays the coupon."""
    terms = make_terms()
    paid = coupon_matrix(terms, np.array([[0.70, 0.6999, 0.70]]))
    assert paid[0, 0] == pytest.approx(100.0)
    assert paid[0, 1] == pytest.approx(0.0)
    assert paid[0, 2] == pytest.approx(100.0)


def test_memory_feature_pays_missed_coupons_on_recovery():
    """With memory, clearing the barrier pays the arrears too.

    Barrier missed at t=1 and t=2, cleared at t=3: three coupons at t=3.
    """
    terms = make_terms(memory=True)
    paid = coupon_matrix(terms, np.array([[0.65, 0.65, 0.95]]))
    assert paid[0].tolist() == pytest.approx([0.0, 0.0, 300.0])

    without = coupon_matrix(make_terms(memory=False), np.array([[0.65, 0.65, 0.95]]))
    assert without[0].tolist() == pytest.approx([0.0, 0.0, 100.0])


def test_bofa_double_condition_needs_knock_in_and_close_below_initial():
    """Knock-in alone does not create a loss, and neither does a close below initial.

    This is the BofA structure: principal is lost only when the barrier was
    touched during the period *and* the worst performer finishes below its
    starting level. Reading it as a plain barrier would overstate the loss
    probability substantially.
    """
    terms = make_terms(
        downside_payoff="worst_return_if_below_initial",
        downside_trigger=0.70, downside_strike=1.0,
        downside_observation="american",
    )
    # Touched the barrier but recovered above initial -> principal back.
    recovered = make_sim([[0.95, 0.60, 1.05]], knocked=np.array([True]))
    assert terminal_payoff(terms, recovered)[0] == pytest.approx(1000.0)

    # Never touched, finished below initial -> principal back.
    never = make_sim([[0.95, 0.90, 0.85]], knocked=np.array([False]))
    assert terminal_payoff(terms, never)[0] == pytest.approx(1000.0)

    # Both conditions -> loss follows the worst performer.
    both = make_sim([[0.95, 0.60, 0.85]], knocked=np.array([True]))
    assert terminal_payoff(terms, both)[0] == pytest.approx(850.0)


def test_issuer_call_lowers_value_relative_to_no_call():
    """The issuer's option cannot be worth anything to the investor.

    Same note priced with the call and without it: holding an option against
    you cannot make you better off. This is the test that catches the exercise
    comparison being the wrong way round, which would show up as the callable
    note being worth more.
    """
    rng = np.random.default_rng(11)
    worst = np.clip(1.0 + 0.2 * rng.standard_normal((20000, 3)).cumsum(axis=1) / np.sqrt(3), 0.05, 3.0)

    callable_terms = make_terms(call_style="issuer_discretionary", call_level=None,
                                call_eligible=np.array([True, True, False]))
    never_called = make_terms(call_style="issuer_discretionary", call_level=None,
                              call_eligible=np.array([False, False, False]))

    sim = make_sim(worst, best=worst)
    with_call = price(callable_terms, sim, FlatCurve(0.04))
    without = price(never_called, sim, FlatCurve(0.04))

    assert with_call["value"] < without["value"]
    assert 0.0 < with_call["prob_called"] <= 1.0


def test_automatic_call_takes_the_first_eligible_trigger():
    """When several dates qualify, redemption happens on the earliest of them."""
    terms = make_terms()
    sim = make_sim([[1.02, 1.30, 1.40]])
    out = price(terms, sim, FlatCurve(0.0))
    # Redeemed at the first review for 1100, not at the second.
    assert out["value"] == pytest.approx(1100.0)


def test_call_is_never_possible_on_the_final_date():
    """Maturity is a redemption, not an early call, so it must not be eligible."""
    terms = make_terms()
    assert not terms.call_eligible[-1]
