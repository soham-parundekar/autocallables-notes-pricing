"""Correlation estimation, repair, and the properties the simulation relies on.

Three things have to hold. The returns used must not reach past the note's
pricing date, or future information enters a historical valuation. The repaired
matrix must be a valid correlation matrix, not merely closer to one. And the
factor handed to the simulation must reproduce the matrix it came from, which is
the property the correlated draws depend on and the one that failed first:
Cholesky needs positive definiteness, and a bumped matrix at the saturation
boundary is only semidefinite.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pricer.correlation import (
    bump_correlation,
    cholesky,
    correlation_matrix,
    higham_nearest_correlation,
    log_returns,
)

RAW = ROOT / "data/raw"
pytestmark = pytest.mark.skipif(
    not (RAW / "fred_SP500.csv").exists(), reason="daily history not downloaded"
)


def test_returns_never_reach_past_the_pricing_date():
    """The snapshot is 2026-10-09 but five notes priced on the 7th.

    Using everything available would feed two days of future returns into a
    pricing-date decision. The window must stop at the pricing date.
    """
    returns = log_returns(["NDX", "RUT", "SPX"], RAW, as_of=date(2026, 10, 7), lookback=252)
    assert returns.index.max().date() <= date(2026, 10, 7)
    assert len(returns) == 252


def test_dates_are_intersected_not_forward_filled():
    """Every row must be a day all three underlyings actually traded.

    Carrying a stale level forward creates a zero return on one asset against a
    real one on another, which drags correlation toward zero for no reason.
    """
    returns = log_returns(["NDX", "RUT", "SPX"], RAW, as_of=date(2026, 10, 7), lookback=252)
    assert returns.notna().all().all()
    # A forward-filled series shows exact zeros; genuine closes essentially never do.
    assert (returns == 0.0).sum().sum() <= 2


def test_estimated_matrix_is_valid_and_plausible():
    matrix, diagnostics = correlation_matrix(
        ["NDX", "RUT", "SPX"], RAW, as_of=date(2026, 10, 7), lookback=252
    )
    assert np.allclose(np.diag(matrix), 1.0)
    assert np.allclose(matrix, matrix.T)
    assert np.linalg.eigvalsh(matrix).min() >= -1e-12
    off_diagonal = matrix[np.triu_indices(3, 1)]
    assert np.all(off_diagonal > 0.3), "US equity indices should be strongly positively related"
    assert np.all(off_diagonal < 1.0)
    assert diagnostics["observations"] == 252


def test_higham_returns_a_valid_correlation_matrix():
    """A deliberately invalid matrix must come back valid, and close by."""
    # Mildly invalid, which is what a sample matrix or a bumped one actually
    # looks like. A wildly inconsistent input would need a large correction, and
    # asserting a small one there would be testing the input, not the method.
    broken = np.array([
        [1.00, 0.82, 0.78],
        [0.82, 1.00, 0.99],
        [0.78, 0.99, 1.00],
    ])
    broken[0, 2] = broken[2, 0] = 0.20
    assert np.linalg.eigvalsh(broken).min() < 0, "test input should be invalid"

    repaired, iterations = higham_nearest_correlation(broken)
    assert np.allclose(np.diag(repaired), 1.0)
    assert np.allclose(repaired, repaired.T)
    assert np.linalg.eigvalsh(repaired).min() >= -1e-9
    assert iterations >= 1

    # Nearest, not merely feasible: the repair must beat an obvious valid
    # alternative. The identity is valid and far away, so a method that simply
    # returned something feasible would fail this.
    identity = np.eye(3)
    assert (
        np.linalg.norm(repaired - broken, ord="fro")
        < np.linalg.norm(identity - broken, ord="fro")
    )
    assert np.linalg.norm(repaired - broken, ord="fro") < 0.5


def test_factor_reproduces_the_matrix_even_when_singular():
    """L @ L.T must equal the input, including at the saturation boundary.

    A uniform +0.10 bump on an NDX/SPX correlation of 0.928 clips at 0.999, and
    the nearest valid matrix to that has an eigenvalue at zero. Cholesky raises
    on it, so the factorisation falls back to the symmetric square root.
    """
    matrix, _ = correlation_matrix(
        ["NDX", "RUT", "SPX"], RAW, as_of=date(2026, 10, 7), lookback=252
    )
    for shift in (0.0, 0.05, -0.05, 0.10, 0.20, -0.20):
        bumped, _, _ = bump_correlation(matrix, shift)
        factor = cholesky(bumped)
        assert np.allclose(factor @ factor.T, bumped, atol=1e-8), f"shift {shift} broke the factor"


def test_bump_reports_the_shift_that_actually_landed():
    """Clipping and repair shrink the bump, and the Greek must know by how much."""
    matrix, _ = correlation_matrix(
        ["NDX", "RUT", "SPX"], RAW, as_of=date(2026, 10, 7), lookback=252
    )
    small, _, achieved_small = bump_correlation(matrix, 0.02)
    assert achieved_small == pytest.approx(0.02, abs=1e-9)

    _, repaired, achieved_large = bump_correlation(matrix, 0.30)
    # NDX/SPX sits at 0.928, so a 0.30 shift cannot all land.
    assert achieved_large < 0.30
    assert repaired or achieved_large < 0.30


def test_bumping_preserves_symmetry_and_unit_diagonal():
    matrix, _ = correlation_matrix(
        ["DJX", "NDX", "RUT"], RAW, as_of=date(2026, 10, 7), lookback=252
    )
    for shift in (0.05, -0.05, 0.15):
        bumped, _, _ = bump_correlation(matrix, shift)
        assert np.allclose(np.diag(bumped), 1.0)
        assert np.allclose(bumped, bumped.T)
        assert np.all(np.abs(bumped) <= 1.0 + 1e-12)
