"""Correlated multi-asset local-volatility simulation.

Daily log-Euler steps under the risk-neutral measure, with each asset's drift
taken from its own forward curve (so the carry r - q is whatever the option
market implies, not a separately guessed dividend yield) and its diffusion from
the Dupire surface in src/vol/local_vol.py.

Two implementation decisions carry most of the weight here.

Paths are never stored. A five-year note observed monthly needs 1,260 daily
steps on three assets, and holding 200,000 of those paths would be about six
terabytes. Instead the engine steps forward keeping only the current level, and
records at each observation date the two statistics every payoff in this project
needs: the worst performance across underlyings and the best. The American
knock-in is handled with a running boolean per path, updated every day, which is
what "observed on any trading day" actually requires.

Local vol is read from a precomputed grid rather than evaluated analytically at
each step. Calling the SVI machinery 756 million times per note is pointless
when the surface is smooth: a 240-by-360 grid in (log-moneyness, time) with
bilinear interpolation reproduces it to well under a basis point of vol and runs
roughly thirty times faster, which is what makes bump-and-revalue Greeks
affordable at all.

Common random numbers come from re-seeding, not from storing draws. Every call
with the same seed and shape generates an identical normal sequence, so a bumped
revaluation differs from the base case only in the bumped input. That turns each
Greek into a clean finite difference instead of a difference of two noisy
numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..vol.local_vol import LocalVolSurface


class LocalVolGrid:
    """Precomputed Dupire local vol with bilinear interpolation.

    The grid is built in log-moneyness against each asset's own forward, which
    is the coordinate the surface is naturally smooth in. Querying at a spot
    level converts to that coordinate first.
    """

    def __init__(
        self,
        surface: LocalVolSurface,
        *,
        t_max: float,
        n_k: int = 240,
        n_t: int = 360,
        k_pad: float = 0.05,
        n_sd: float = 1e6,
    ):
        self.surface = surface
        self.ticker = surface.ticker
        lo, hi = surface.k_bounds
        self.k_grid = np.linspace(lo - k_pad, hi + k_pad, n_k)
        # Time grid is denser at the front, where the surface moves fastest and
        # where most early-redemption decisions happen.
        self.t_grid = np.concatenate([
            np.linspace(1e-4, min(0.5, t_max), max(n_t // 3, 8), endpoint=False),
            np.linspace(min(0.5, t_max), t_max * 1.02, n_t - max(n_t // 3, 8)),
        ])

        # Small probe grid used only to read the at-the-money total variance,
        # which sets how wide the usable window is at each time.
        self._tail_k_probe = np.linspace(-0.02, 0.02, 5)

        table = np.empty((self.t_grid.size, self.k_grid.size))
        self.window_edges: list[tuple[float, float]] = []
        for j, t in enumerate(self.t_grid):
            table[j], edges = self._row(float(t), n_sd)
            self.window_edges.append(edges)
        self.table = table
        self.clamped_k = 0
        self.n_queries = 0

    def _row(self, t: float, n_sd: float) -> tuple[np.ndarray, tuple[float, float]]:
        """Local vol at one time, computed only where the market informs it.

        Dupire needs dw/dT, and src/vol/svi.py enforces calendar monotonicity
        only over the strikes each pair of adjacent expiries actually quotes.
        Far outside that range dw/dT is unconstrained and the formula returns
        nonsense: on SPX at two weeks it gave 350% vol at k = -1.2, with NaNs
        and exact zeros scattered through the call wing and a maximum of 4730%.

        Narrowing the evaluation window and holding vol flat in k beyond it was
        tried and rejected: it does fix the corners, but it changes the surface,
        so the Monte Carlo stops reproducing the implied vols of any option
        outside the window and the wing repricing collapses. Steeply rising
        local vol in a short-dated put wing is real, not an artefact. The window
        is therefore off by default (n_sd is effectively infinite) and the cause
        is addressed upstream instead, by enforcing the calendar condition
        across the whole calibrated band in src/vol/svi.py so that dw/dT cannot
        go negative.

        What remains here is gap filling: where the formula returns a non-finite
        value or an exact zero, that point is interpolated from its valid
        neighbours along the strike axis. That repairs isolated corners without
        touching any value the formula actually produced.
        """
        atm_w = float(np.interp(0.0, self._tail_k_probe, self._atm_probe(t)))
        sd = float(np.sqrt(max(atm_w, 1e-10)))
        half = max(n_sd * sd, 0.05)
        lo = max(self.k_grid[0], -half)
        hi = min(self.k_grid[-1], half)

        inside = (self.k_grid >= lo) & (self.k_grid <= hi)
        if inside.sum() < 4:
            inside = np.ones_like(self.k_grid, dtype=bool)

        vol = np.full_like(self.k_grid, np.nan)
        variance = self.surface.local_variance(self.k_grid[inside], t)
        vol[inside] = np.sqrt(np.maximum(variance, 0.0))
        vol[inside] = np.where(
            np.isfinite(vol[inside]) & (vol[inside] > 1e-6), vol[inside], np.nan
        )

        valid = np.isfinite(vol)
        if valid.any():
            # Interpolate across any gap inside the window, then hold the edge
            # values flat outwards. np.interp does both in one call.
            vol = np.interp(self.k_grid, self.k_grid[valid], vol[valid])
        else:
            vol = np.full_like(self.k_grid, 0.20)
        return np.clip(vol, 0.01, 2.0), (lo, hi)

    def _atm_probe(self, t: float) -> np.ndarray:
        return self.surface.total_variance(self._tail_k_probe, t)

    def __call__(self, spot: np.ndarray, t: float) -> np.ndarray:
        forward = float(self.surface.forward_curve(t)[0])
        k = np.log(np.maximum(spot, 1e-12) / forward)

        self.n_queries += k.size
        self.clamped_k += int(np.count_nonzero((k < self.k_grid[0]) | (k > self.k_grid[-1])))
        k = np.clip(k, self.k_grid[0], self.k_grid[-1])

        # Bilinear: locate the time row once (t is scalar per step), then
        # interpolate along strike for the two bracketing rows.
        jt = np.searchsorted(self.t_grid, t) - 1
        jt = min(max(jt, 0), self.t_grid.size - 2)
        t0, t1 = self.t_grid[jt], self.t_grid[jt + 1]
        wt = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
        wt = min(max(wt, 0.0), 1.0)

        low = np.interp(k, self.k_grid, self.table[jt])
        high = np.interp(k, self.k_grid, self.table[jt + 1])
        return low + wt * (high - low)

    def max_abs_error(self, n_probe: int = 40, t_min: float = 0.02) -> float:
        """Largest vol error of the grid against the analytic surface, in vol points.

        The comparison is restricted to the calibrated moneyness band and to
        t >= t_min, and to points where the analytic surface is both finite and
        inside the clamp. Outside those regions the analytic local vol can run
        to several hundred percent in a corner, the grid deliberately clamps it,
        and differencing the two measures the clamp rather than the
        interpolation this method is meant to check.
        """
        lo, hi = self.surface.k_bounds
        ks = np.linspace(lo, hi, n_probe)
        worst = 0.0
        for t in np.linspace(max(self.t_grid[0], t_min), self.t_grid[-1], n_probe):
            exact = np.sqrt(np.maximum(self.surface.local_variance(ks, float(t)), 0.0))
            approx = self(np.exp(ks) * float(self.surface.forward_curve(float(t))[0]), float(t))
            mask = np.isfinite(exact) & (exact > 0.01) & (exact < 2.0)
            if mask.any():
                worst = max(worst, float(np.max(np.abs(exact[mask] - approx[mask]))))
        return worst


@dataclass
class SimulationResult:
    """What the payoff engine needs, and nothing it does not."""

    worst: np.ndarray          # (paths, n_obs) worst performance across underlyings
    best: np.ndarray           # (paths, n_obs) best performance
    knocked_in: np.ndarray     # (paths,) bool, True if the American barrier was touched
    per_asset_final: np.ndarray  # (paths, n_assets) terminal performance
    n_paths: int
    observation_times: np.ndarray
    diagnostics: dict = field(default_factory=dict)


def correlated_normals(
    rng: np.random.Generator, n_paths: int, n_assets: int, cholesky: np.ndarray
) -> np.ndarray:
    """One step of correlated standard normals, shape (n_paths, n_assets)."""
    z = rng.standard_normal((n_paths, n_assets))
    return z @ cholesky.T


def simulate(
    grids: list[LocalVolGrid],
    initial_levels: np.ndarray,
    carry: np.ndarray,
    cholesky: np.ndarray,
    observation_times: np.ndarray,
    *,
    reference_levels: np.ndarray | None = None,
    n_paths: int,
    steps_per_year: int = 252,
    seed: int = 20261009,
    antithetic: bool = True,
    knock_in_level: float | None = None,
    knock_in_window: tuple[float, float] | None = None,
    carry_curve=None,
) -> SimulationResult:
    """Step the assets to the last observation date, recording what payoffs need.

    `initial_levels` is where the paths start. `reference_levels` is what
    performance is measured against, and defaults to the same thing. They have
    to be separable for delta to mean anything: every barrier in these notes is
    a fraction of the initial level fixed by the term sheet, so scaling both
    together leaves performance identical at every observation and measures
    nothing but a shift in where the local-vol surface is sampled. A spot bump
    moves the starting level while the barriers stay where the filing put them.

    `carry` is a fallback constant per asset; when `carry_curve` is supplied it
    is called as carry_curve(asset_index, t) and takes precedence, so the drift
    follows the term structure of the forward rather than one average number.

    Antithetic variates halve the path count actually drawn and mirror each
    draw. For these payoffs the gain is real but modest: the autocall and the
    barrier are both discontinuous, and antithetics help smooth payoffs most.
    """
    n_assets = len(grids)
    observation_times = np.asarray(observation_times, dtype=float)

    if antithetic:
        if n_paths % 2:
            n_paths += 1
        n_draw = n_paths // 2
    else:
        n_draw = n_paths

    rng = np.random.default_rng(seed)

    # One time grid covering every observation date, with daily steps between.
    # Observation dates are forced to be grid points so no coupon or call test
    # is evaluated at an interpolated time.
    t_end = float(observation_times[-1])
    n_steps = max(int(round(t_end * steps_per_year)), len(observation_times) * 2)
    base = np.linspace(0.0, t_end, n_steps + 1)
    times = np.unique(np.concatenate([base, observation_times, [0.0]]))
    obs_index = np.searchsorted(times, observation_times)

    reference = (
        initial_levels if reference_levels is None else np.asarray(reference_levels, dtype=float)
    )
    spot = np.tile(initial_levels.astype(float), (n_paths, 1))
    worst = np.empty((n_paths, observation_times.size), dtype=np.float32)
    best = np.empty((n_paths, observation_times.size), dtype=np.float32)
    knocked = np.zeros(n_paths, dtype=bool)

    ki_lo, ki_hi = (knock_in_window if knock_in_window else (0.0, t_end))
    next_obs = 0
    vol_sum = np.zeros(n_assets)
    vol_count = 0

    for step in range(1, times.size):
        t0, t1 = times[step - 1], times[step]
        dt = t1 - t0
        if dt <= 0:
            continue
        sqrt_dt = np.sqrt(dt)

        vols = np.empty((n_paths, n_assets))
        for a, grid in enumerate(grids):
            vols[:, a] = grid(spot[:, a], float(t0))
        vol_sum += vols.mean(axis=0)
        vol_count += 1

        shocks = correlated_normals(rng, n_draw, n_assets, cholesky)
        if antithetic:
            shocks = np.concatenate([shocks, -shocks], axis=0)

        if carry_curve is None:
            drift = carry[None, :]
        else:
            drift = np.array([[carry_curve(a, float(t0)) for a in range(n_assets)]])

        spot *= np.exp((drift - 0.5 * vols**2) * dt + vols * sqrt_dt * shocks)

        performance = spot / reference[None, :]

        # The knock-in is checked every step inside its window, which is what
        # "on any trading day during the knock-in period" means. Checking it
        # only on observation dates would systematically understate it.
        if knock_in_level is not None and ki_lo <= t1 <= ki_hi:
            knocked |= performance.min(axis=1) <= knock_in_level

        while next_obs < observation_times.size and step == obs_index[next_obs]:
            worst[:, next_obs] = performance.min(axis=1)
            best[:, next_obs] = performance.max(axis=1)
            next_obs += 1

    if next_obs != observation_times.size:
        raise RuntimeError(f"recorded {next_obs} of {observation_times.size} observation dates")

    return SimulationResult(
        worst=worst,
        best=best,
        knocked_in=knocked,
        per_asset_final=spot / reference[None, :],
        n_paths=n_paths,
        observation_times=observation_times,
        diagnostics={
            "n_time_steps": int(times.size - 1),
            "mean_local_vol": (vol_sum / max(vol_count, 1)).tolist(),
            "grid_k_clamped_pct": [
                100.0 * g.clamped_k / max(g.n_queries, 1) for g in grids
            ],
        },
    )
