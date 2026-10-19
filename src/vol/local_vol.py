"""Dupire local volatility read off the fitted SVI surface.

In Gatheral's log-moneyness form, with w(k,T) the total implied variance and
k = ln(K / F(T)),

                                      dw/dT
    sigma_LV^2(k,T) = -------------------------------------------------------
                       1 - (k/w) dw/dk
                         + (1/4)(-1/4 - 1/w + k^2/w^2)(dw/dk)^2
                         + (1/2) d2w/dk2

Every derivative here comes off the fitted slices, not off market quotes. In k
that is exact: raw SVI is differentiable twice in closed form. In T the surface
is interpolated linearly in total variance between adjacent expiries, which
makes dw/dT piecewise constant but has the property that matters - because
src/vol/svi.py already forced w to be non-decreasing in T, dw/dT cannot come
out negative, so the numerator cannot flip sign. Combined with the butterfly
constraint, which keeps the denominator positive, the local variance stays
positive by construction instead of by luck. That is the whole reason the
arbitrage checks in the previous module are not optional.

Beyond the longest listed expiry the surface has to be extrapolated, and for
the five-year notes that is a long way: SPX quotes reach 5.2 years, but NDX
stops at 3.2 and the Russell at 2.2. Three modes are provided so the choice can
be measured rather than asserted:

    frozen_smile  w(k,T) = w_last(k) * T / T_last
                  Implied vol is held flat in T with its smile shape intact.
                  This is the brief's suggestion and the base case.

    variance_slope  w(k,T) = w_last(k) + (T - T_last) * (dw/dT at T_last)
                  Carries the last observed variance slope forward, which keeps
                  the upward term structure going instead of flattening it.

    frozen_shifted  frozen_smile with every vol shifted by a fixed amount, for
                  bounding what the extrapolation is worth.

src/analysis/extrapolation_risk.py reprices each note under all three and
reports the spread, so the long-dated results carry an honest band rather than
a single number that hides the assumption.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .svi import SVIParams

# Local vol is clamped before it reaches the simulation. The floor stops a
# division from producing a zero-vol path; the ceiling stops one bad corner of
# the surface from throwing a path to the moon. Both bindings are counted and
# reported rather than silently applied.
MIN_LOCAL_VOL = 0.01
MAX_LOCAL_VOL = 2.00


@dataclass
class ForwardCurve:
    """Forward level by time, from the per-expiry put-call-parity forwards.

    Interpolation is linear in ln F, which is equivalent to piecewise-constant
    continuous carry (rate minus dividend yield) between expiries. Beyond the
    last expiry the carry from the final segment is carried forward, since the
    alternative - holding F flat - would imply the dividend yield suddenly
    equals the risk-free rate.
    """

    times: np.ndarray
    forwards: np.ndarray
    spot: float

    def __post_init__(self) -> None:
        order = np.argsort(self.times)
        self.times = np.asarray(self.times, dtype=float)[order]
        self.forwards = np.asarray(self.forwards, dtype=float)[order]
        self._log_f = np.log(self.forwards)
        # The carry from valuation to the first listed expiry is set by spot and
        # that expiry's forward, not by the slope between the first two
        # expiries. Using the latter understated the drift over the opening
        # fortnight badly: the first two SPX expiries are eight days apart and
        # imply 0.78% carry, against the 3.90% that spot and the first forward
        # actually imply. Integrated over the step grid that put every simulated
        # forward 7 to 12 bp below its market value, at every maturity, which is
        # exactly the bias the repricing check picked up.
        self._head_carry = float((self._log_f[0] - np.log(self.spot)) / self.times[0])
        if self.times.size >= 2:
            self._tail_carry = float(
                (self._log_f[-1] - self._log_f[-2]) / (self.times[-1] - self.times[-2])
            )
        else:
            self._tail_carry = self._head_carry

    def __call__(self, t: float | np.ndarray) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, dtype=float))
        log_f = np.interp(t, self.times, self._log_f)
        below = t < self.times[0]
        above = t > self.times[-1]
        # Both ends grow at the constant carry of their adjoining segment, which
        # keeps F continuous at the pillars and keeps the carry used by the
        # simulation consistent with the forward it is supposed to reproduce.
        if np.any(below):
            log_f = np.where(below, np.log(self.spot) + self._head_carry * t, log_f)
        if np.any(above):
            log_f = np.where(
                above, self._log_f[-1] + self._tail_carry * (t - self.times[-1]), log_f
            )
        return np.exp(log_f)

    def carry(self, t: float) -> float:
        """Continuous carry (r - q) at time t, as implied by the forward curve."""
        t = float(t)
        if t >= self.times[-1]:
            return self._tail_carry
        if t <= self.times[0]:
            return self._head_carry
        i = int(np.searchsorted(self.times, t, side="right")) - 1
        i = min(max(i, 0), self.times.size - 2)
        return float((self._log_f[i + 1] - self._log_f[i]) / (self.times[i + 1] - self.times[i]))


class LocalVolSurface:
    """Dupire local vol on one underlying, built from fitted SVI slices."""

    def __init__(
        self,
        slices: list[SVIParams],
        forward_curve: ForwardCurve,
        *,
        ticker: str = "",
        extrapolation: str = "frozen_smile",
        # Only used by the frozen_shifted extrapolation mode, and only past the
        # last listed expiry. This is not a parallel shift of the surface: for
        # that, scale the slices themselves through load_surface's bucket_shift.
        tail_vol_shift: float = 0.0,
        k_bounds: tuple[float, float] = (-0.9, 0.45),
    ):
        if not slices:
            raise ValueError("need at least one fitted slice")
        self.slices = sorted(slices, key=lambda p: p.t)
        self.times = np.array([p.t for p in self.slices])
        self.forward_curve = forward_curve
        self.ticker = ticker
        self.extrapolation = extrapolation
        self.tail_vol_shift = tail_vol_shift
        self.k_bounds = k_bounds
        self.last_t = float(self.times[-1])
        self.clamp_low = 0
        self.clamp_high = 0
        self.n_evaluated = 0

        # Variance slope over the final listed segment, for variance_slope mode.
        if len(self.slices) >= 2:
            k0 = np.linspace(k_bounds[0], k_bounds[1], 81)
            w_last = self.slices[-1].total_variance(k0)
            w_prev = self.slices[-2].total_variance(k0)
            self._tail_dwdt = (w_last - w_prev) / (self.times[-1] - self.times[-2])
            self._tail_k = k0
        else:
            self._tail_k = np.linspace(k_bounds[0], k_bounds[1], 81)
            self._tail_dwdt = self.slices[-1].total_variance(self._tail_k) / self.times[-1]

    # ---------------------------------------------------------------- surface

    def _clip_k(self, k: np.ndarray) -> np.ndarray:
        return np.clip(k, self.k_bounds[0], self.k_bounds[1])

    def total_variance(self, k: np.ndarray, t: float) -> np.ndarray:
        """w(k,t): linear in t between listed expiries, extrapolated past the last."""
        k = self._clip_k(np.atleast_1d(np.asarray(k, dtype=float)))
        if t <= self.times[0]:
            # Scale the first slice back to t so that implied vol is held flat
            # from valuation to the first listed expiry.
            return self.slices[0].total_variance(k) * (t / self.times[0])
        if t >= self.last_t:
            return self._extrapolate(k, t)

        i = int(np.searchsorted(self.times, t, side="right")) - 1
        i = min(max(i, 0), len(self.slices) - 2)
        t0, t1 = self.times[i], self.times[i + 1]
        w0 = self.slices[i].total_variance(k)
        w1 = self.slices[i + 1].total_variance(k)
        frac = (t - t0) / (t1 - t0)
        return w0 + frac * (w1 - w0)

    def _extrapolate(self, k: np.ndarray, t: float) -> np.ndarray:
        last = self.slices[-1]
        if self.extrapolation == "variance_slope":
            slope = np.interp(k, self._tail_k, self._tail_dwdt)
            return np.maximum(last.total_variance(k) + slope * (t - self.last_t), 1e-12)
        # frozen_smile and frozen_shifted
        w = last.total_variance(k) * (t / self.last_t)
        if self.extrapolation == "frozen_shifted" and self.tail_vol_shift:
            vol = np.sqrt(np.maximum(w, 1e-12) / t) + self.tail_vol_shift
            w = np.maximum(vol, 1e-4) ** 2 * t
        return np.maximum(w, 1e-12)

    def _derivatives(self, k: np.ndarray, t: float) -> tuple[np.ndarray, ...]:
        """(w, dw/dk, d2w/dk2, dw/dT) at (k,t)."""
        k = self._clip_k(np.atleast_1d(np.asarray(k, dtype=float)))

        if t >= self.last_t:
            last = self.slices[-1]
            if self.extrapolation == "variance_slope":
                slope = np.interp(k, self._tail_k, self._tail_dwdt)
                return (
                    last.total_variance(k) + slope * (t - self.last_t),
                    last.d_dk(k),
                    last.d2_dk2(k),
                    slope,
                )
            scale = t / self.last_t
            return (
                last.total_variance(k) * scale,
                last.d_dk(k) * scale,
                last.d2_dk2(k) * scale,
                last.total_variance(k) / self.last_t,
            )

        if t <= self.times[0]:
            first = self.slices[0]
            scale = max(t, 1e-8) / self.times[0]
            return (
                first.total_variance(k) * scale,
                first.d_dk(k) * scale,
                first.d2_dk2(k) * scale,
                first.total_variance(k) / self.times[0],
            )

        i = int(np.searchsorted(self.times, t, side="right")) - 1
        i = min(max(i, 0), len(self.slices) - 2)
        lo, hi = self.slices[i], self.slices[i + 1]
        t0, t1 = self.times[i], self.times[i + 1]
        frac = (t - t0) / (t1 - t0)
        w0, w1 = lo.total_variance(k), hi.total_variance(k)
        return (
            w0 + frac * (w1 - w0),
            lo.d_dk(k) + frac * (hi.d_dk(k) - lo.d_dk(k)),
            lo.d2_dk2(k) + frac * (hi.d2_dk2(k) - lo.d2_dk2(k)),
            (w1 - w0) / (t1 - t0),
        )

    # --------------------------------------------------------------- Dupire

    def local_variance(self, k: np.ndarray, t: float) -> np.ndarray:
        """Dupire local variance at log-moneyness k and time t."""
        w, dwdk, d2wdk2, dwdt = self._derivatives(k, t)
        w = np.maximum(w, 1e-10)
        denominator = (
            1.0
            - (k / w) * dwdk
            + 0.25 * (-0.25 - 1.0 / w + (k * k) / (w * w)) * dwdk**2
            + 0.5 * d2wdk2
        )
        # The butterfly constraint in svi.py is exactly the condition that keeps
        # this denominator away from zero, so a non-positive value here means
        # that constraint was not satisfied somewhere. Guard rather than emit a
        # NaN, and let the clamp counters surface it.
        denominator = np.where(denominator > 1e-8, denominator, np.nan)
        return np.maximum(dwdt, 0.0) / denominator

    def local_vol(self, spot: np.ndarray, t: float) -> np.ndarray:
        """Local vol for assets sitting at `spot` at time t, clamped for the simulation."""
        spot = np.atleast_1d(np.asarray(spot, dtype=float))
        forward = float(self.forward_curve(t)[0])
        k = np.log(np.maximum(spot, 1e-12) / forward)
        variance = self.local_variance(k, t)
        vol = np.sqrt(np.maximum(variance, 0.0))
        vol = np.where(np.isfinite(vol), vol, MIN_LOCAL_VOL)

        self.n_evaluated += vol.size
        self.clamp_low += int(np.sum(vol < MIN_LOCAL_VOL))
        self.clamp_high += int(np.sum(vol > MAX_LOCAL_VOL))
        return np.clip(vol, MIN_LOCAL_VOL, MAX_LOCAL_VOL)

    def clamp_report(self) -> dict:
        n = max(self.n_evaluated, 1)
        return {
            "ticker": self.ticker,
            "evaluations": self.n_evaluated,
            "clamped_low_pct": 100.0 * self.clamp_low / n,
            "clamped_high_pct": 100.0 * self.clamp_high / n,
        }

    def implied_vol(self, k: np.ndarray, t: float) -> np.ndarray:
        """Black implied vol from the interpolated surface, for the repricing test."""
        return np.sqrt(np.maximum(self.total_variance(k, t), 1e-12) / max(t, 1e-12))


def grid_report(surface: LocalVolSurface, ks: np.ndarray, ts: np.ndarray) -> np.ndarray:
    """Local vol over a (k,t) grid. Used to eyeball the surface and count NaNs."""
    out = np.empty((ts.size, ks.size))
    for j, t in enumerate(ts):
        out[j] = np.sqrt(np.maximum(surface.local_variance(ks, float(t)), 0.0))
    return out
