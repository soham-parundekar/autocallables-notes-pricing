"""Correlation between the note underlyings, estimated from returns and repaired if needed.

Worst-of notes live or die on correlation, so this module is small but it is
where a lot of the price comes from. Three things it is careful about.

Look-ahead. Returns are taken up to and including the note's own pricing date,
never to the snapshot date. Five of the six notes priced on 7 October and the
option snapshot is from the 9th, so using everything available would feed two
days of future returns into a historical pricing decision. The difference to the
matrix is small; the principle is not.

Realised, not implied. There is no free source of implied correlation, and
vanilla options carry none: a quote on one index says nothing about its
co-movement with another. So this is a historical estimate standing in for a
risk-neutral input, which is the single largest modelling approximation in the
project and is listed as such in the report. Dealers mark correlation above
realised for exactly this product, because selling a worst-of leaves them short
correlation, so a realised estimate is likely to understate what the issuer
used.

Validity. A sample correlation matrix from overlapping windows with missing days
can come back slightly non-positive-semidefinite, and Cholesky then fails or,
worse, succeeds on a matrix that implies negative variance for some portfolio.
Higham's (2002) alternating-projection method finds the nearest valid
correlation matrix in the Frobenius norm, and it is applied whenever the
smallest eigenvalue is negative.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# Where each underlying's daily closing level comes from. Index levels are
# preferred over their ETF proxies here even where the option surface uses the
# proxy, because the note's payoff is defined on the index itself.
SERIES = {
    "SPX": ("fred_SP500.csv", "observation_date", "SP500"),
    "NDX": ("fred_NASDAQ100.csv", "observation_date", "NASDAQ100"),
    "DJX": ("fred_DJIA.csv", "observation_date", "DJIA"),
    "RUT": ("cboe_rut_history.csv", "DATE", "RUT"),
    "SMH": ("yahoo_SMH_daily.csv", "Date", "Close"),
}


def load_levels(ticker: str, raw_dir: Path) -> pd.Series:
    """Daily closing levels for one underlying, indexed by date."""
    filename, date_col, value_col = SERIES[ticker]
    frame = pd.read_csv(raw_dir / filename)
    frame[date_col] = pd.to_datetime(frame[date_col], format="mixed")
    series = (
        frame.set_index(date_col)[value_col]
        .apply(pd.to_numeric, errors="coerce")
        .dropna()
        .sort_index()
    )
    series.name = ticker
    return series


def log_returns(
    tickers: list[str], raw_dir: Path, *, as_of: date, lookback: int = 252
) -> pd.DataFrame:
    """Aligned daily log returns ending on or before `as_of`.

    Dates are intersected across underlyings rather than forward-filled. A
    carried-forward level produces a zero return on one asset against a real
    return on another, which biases correlation toward zero for no reason.
    """
    levels = pd.concat([load_levels(t, raw_dir) for t in tickers], axis=1, join="inner")
    levels = levels.loc[levels.index <= pd.Timestamp(as_of)]
    if len(levels) < lookback + 1:
        raise ValueError(
            f"only {len(levels)} aligned observations on or before {as_of}, need {lookback + 1}"
        )
    window = levels.iloc[-(lookback + 1):]
    return np.log(window / window.shift(1)).dropna()


def higham_nearest_correlation(
    matrix: np.ndarray, *, max_iter: int = 200, tol: float = 1e-10
) -> tuple[np.ndarray, int]:
    """Nearest valid correlation matrix by alternating projections (Higham 2002).

    Projects alternately onto the set of matrices with unit diagonal and onto
    the positive semidefinite cone, with Dykstra's correction so the iteration
    converges to the nearest point rather than merely to a feasible one.
    """
    n = matrix.shape[0]
    correction = np.zeros_like(matrix)
    current = matrix.copy()

    for iteration in range(1, max_iter + 1):
        adjusted = current - correction
        eigenvalues, eigenvectors = np.linalg.eigh(adjusted)
        psd = (eigenvectors * np.maximum(eigenvalues, 0.0)) @ eigenvectors.T
        correction = psd - adjusted
        previous, current = current, psd.copy()
        np.fill_diagonal(current, 1.0)
        if np.linalg.norm(current - previous, ord="fro") < tol:
            return current, iteration
    return current, max_iter


def correlation_matrix(
    tickers: list[str],
    raw_dir: Path,
    *,
    as_of: date,
    lookback: int = 252,
) -> tuple[np.ndarray, dict]:
    """Estimate the correlation matrix and repair it if it is not valid."""
    returns = log_returns(tickers, raw_dir, as_of=as_of, lookback=lookback)
    sample = returns.corr().to_numpy()

    eigenvalues = np.linalg.eigvalsh(sample)
    repaired, iterations = (sample, 0)
    if eigenvalues.min() < 0:
        repaired, iterations = higham_nearest_correlation(sample)

    diagnostics = {
        "tickers": tickers,
        "as_of": as_of.isoformat(),
        "observations": int(len(returns)),
        "first_return_date": str(returns.index[0].date()),
        "last_return_date": str(returns.index[-1].date()),
        "min_eigenvalue_sample": float(eigenvalues.min()),
        "min_eigenvalue_repaired": float(np.linalg.eigvalsh(repaired).min()),
        "higham_iterations": iterations,
        "frobenius_shift": float(np.linalg.norm(repaired - sample, ord="fro")),
        "annualised_vol": {
            t: float(returns[t].std(ddof=1) * np.sqrt(252)) for t in tickers
        },
    }
    return repaired, diagnostics


def cholesky(matrix: np.ndarray) -> np.ndarray:
    """A factor L with L @ L.T == matrix, valid even when the matrix is singular.

    Cholesky is tried first because it is cheaper and gives a triangular
    factor. It needs positive *definite* input though, and the matrices that
    arrive here are not always that: bumping NDX/SPX correlation of 0.928 up by
    0.10 saturates at 0.999, and the nearest valid correlation matrix to that is
    positive semidefinite with an eigenvalue at zero. Cholesky raises on it,
    and a jitter large enough to fix that is large enough to change the bump.

    The symmetric square root from the eigendecomposition has no such problem.
    It is not triangular, which costs nothing here: the factor is only ever used
    as z @ L.T to correlate independent normals, and any L with L @ L.T equal to
    the target correlation does that correctly.
    """
    try:
        return np.linalg.cholesky(matrix)
    except np.linalg.LinAlgError:
        pass

    candidate = matrix
    if np.linalg.eigvalsh(candidate).min() < 0:
        candidate, _ = higham_nearest_correlation(candidate)
    try:
        return np.linalg.cholesky(candidate)
    except np.linalg.LinAlgError:
        eigenvalues, eigenvectors = np.linalg.eigh(candidate)
        return eigenvectors * np.sqrt(np.maximum(eigenvalues, 0.0))


def bump_correlation(matrix: np.ndarray, shift: float) -> tuple[np.ndarray, bool, float]:
    """Shift every off-diagonal correlation and repair the result.

    Used for the correlation Greek. A uniform shift of a valid correlation
    matrix is frequently invalid, and clipping at +/-0.999 plus a repair means
    the bump that actually lands is smaller than the one asked for. The realised
    average shift is returned alongside, because a Greek quoted per 0.05 of
    correlation is wrong if only 0.03 of it survived.
    """
    n = matrix.shape[0]
    off = ~np.eye(n, dtype=bool)
    bumped = matrix + shift * off
    bumped = np.clip(bumped, -0.999, 0.999)
    np.fill_diagonal(bumped, 1.0)

    repaired = False
    if np.linalg.eigvalsh(bumped).min() < 0:
        bumped, _ = higham_nearest_correlation(bumped)
        repaired = True

    achieved = float(np.mean(bumped[off] - matrix[off]))
    return bumped, repaired, achieved
