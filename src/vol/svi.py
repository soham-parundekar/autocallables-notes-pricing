"""Raw SVI fitted per expiry, with butterfly and calendar arbitrage enforced.

The surface is fitted in total implied variance w(k) = sigma_BS(k)^2 * T against
log-moneyness k = ln(K/F), using Gatheral's raw parameterisation

    w(k) = a + b * ( rho * (k - m) + sqrt((k - m)^2 + s^2) )

Fitting variance rather than vol matters for what comes next. Dupire needs
dw/dT and d2w/dk2, and SVI is smooth and twice differentiable in closed form,
so those derivatives come off the fitted slice instead of off noisy quotes.

The arbitrage checks are not decoration. A slice with butterfly arbitrage has a
negative risk-neutral density somewhere, and Dupire's denominator goes through
zero there and hands back a negative local variance: that is the single most
common cause of a local-vol surface full of NaNs. So the fit is checked, and
when a check fails the slice is refitted under the constraint rather than
patched afterwards.

Two conditions, following Gatheral and Jacquier (2014):

Butterfly. With g(k) = (1 - k w'/(2w))^2 - (w'^2/4)(1/w + 1/4) + w''/2, the
density is non-negative exactly when g(k) >= 0 everywhere. g is evaluated on a
dense grid spanning well past the quoted strikes, because the wings are where it
fails.

Calendar. Total variance must not decrease in T at fixed k, or a calendar spread
prices negative. Adjacent fitted slices are compared on a common grid, and a
violating slice is refitted subject to w_later(k) >= w_earlier(k).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares, minimize

# Fallback grid, used only when a caller does not supply one.
#
# Arbitrage conditions are checked on a grid scoped to each expiry rather than
# on one fixed wide grid, and that choice is load-bearing. A fixed grid spanning
# k in [-1.5, 1.0] asks a two-week slice about a strike 78% below the forward,
# where it has no quotes and is purely extrapolating. Enforcing the calendar
# condition out there made every short-dated slice's wing extrapolation a floor
# for the next expiry, and the error cascaded: fits that stood at 0.06 vol
# points unconstrained degraded to 7.4 once the floor was applied. Scoped to the
# strikes each pair of expiries actually quotes, the same surface fits cleanly.
CHECK_GRID = np.linspace(-1.5, 1.0, 241)

# How far past the quoted strikes to keep enforcing, and the widest the grid is
# allowed to get in units of total vol to expiry. The cap stops one expiry with
# an unusually deep listed put wing from setting the terms for everything else.
GRID_PAD = 0.10
GRID_MAX_SD = 5.0


def slice_grid(k_data: np.ndarray, w_atm: float, *, n: int = 161) -> np.ndarray:
    """Check grid for one expiry: its quoted strike range, padded and capped."""
    sd = float(np.sqrt(max(w_atm, 1e-8)))
    lo = max(float(k_data.min()) - GRID_PAD, -GRID_MAX_SD * sd)
    hi = min(float(k_data.max()) + GRID_PAD, GRID_MAX_SD * sd)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(k_data.min()), float(k_data.max())
    return np.linspace(lo, hi, n)

# g(k) is allowed to sit marginally below zero before a slice is called
# arbitrageable; exact zeros show up routinely from floating point.
BUTTERFLY_TOL = -1e-8
CALENDAR_TOL = -1e-8

# The calendar floor is imposed at a finite set of points, so a smooth slice can
# satisfy it exactly there and still dip a little below between them. Enforcing
# with a small positive margin, on a denser grid, makes the continuous condition
# hold rather than just the sampled one: without the margin the independent
# audit found dips of 4e-4 in total variance on grids it did not share with the
# fit.
CALENDAR_MARGIN = 1e-6
FLOOR_GRID_POINTS = 241


@dataclass(frozen=True)
class SVIParams:
    """One expiry's raw-SVI parameters."""

    a: float
    b: float
    rho: float
    m: float
    sigma: float
    t: float

    def total_variance(self, k: np.ndarray) -> np.ndarray:
        u = np.asarray(k, dtype=float) - self.m
        return self.a + self.b * (self.rho * u + np.sqrt(u * u + self.sigma**2))

    def d_dk(self, k: np.ndarray) -> np.ndarray:
        u = np.asarray(k, dtype=float) - self.m
        r = np.sqrt(u * u + self.sigma**2)
        return self.b * (self.rho + u / r)

    def d2_dk2(self, k: np.ndarray) -> np.ndarray:
        u = np.asarray(k, dtype=float) - self.m
        r = np.sqrt(u * u + self.sigma**2)
        return self.b * self.sigma**2 / r**3

    def implied_vol(self, k: np.ndarray) -> np.ndarray:
        return np.sqrt(np.maximum(self.total_variance(k), 1e-12) / self.t)

    def as_tuple(self) -> tuple[float, float, float, float, float]:
        return (self.a, self.b, self.rho, self.m, self.sigma)


def butterfly_g(p: SVIParams, k: np.ndarray = CHECK_GRID) -> np.ndarray:
    """Gatheral-Jacquier g(k); non-negative everywhere means no butterfly arbitrage."""
    w = p.total_variance(k)
    wp = p.d_dk(k)
    wpp = p.d2_dk2(k)
    w = np.maximum(w, 1e-12)
    return (1.0 - k * wp / (2.0 * w)) ** 2 - (wp**2 / 4.0) * (1.0 / w + 0.25) + wpp / 2.0


def risk_neutral_density(p: SVIParams, k: np.ndarray = CHECK_GRID) -> np.ndarray:
    """Density in log-strike implied by the slice. Negative values are the smoking gun."""
    w = np.maximum(p.total_variance(k), 1e-12)
    d2 = -k / np.sqrt(w) - 0.5 * np.sqrt(w)
    return butterfly_g(p, k) / np.sqrt(2.0 * np.pi * w) * np.exp(-0.5 * d2**2)


def _inner_linear_fit(
    k: np.ndarray, w: np.ndarray, weight: np.ndarray, m: float, sigma: float
) -> tuple[float, np.ndarray]:
    """Exact weighted fit of (a, b*rho*sigma, b*sigma) for a fixed (m, sigma).

    This is the Zeliade quasi-explicit reduction. Substituting y = (k-m)/sigma,

        w(y) = a + (b sigma rho) y + (b sigma) sqrt(y^2 + 1)

    which is linear in the three coefficients. So instead of pushing five
    parameters around at once, the outer search only has to handle m and sigma
    and the rest is solved exactly. That matters because the full 5-D objective
    has local minima that a plain least-squares run falls into: on the SPX
    expiry at T = 0.36 it converged to rho = +0.39, a positive skew, which is
    the wrong sign for an equity index and left 26 bp of error at the money.

    Constraints kept here are the ones that make the slice a valid SVI slice:
    |rho| <= 1 (so d >= |c|) and w >= 0 everywhere (so a + sqrt(d^2 - c^2) >= 0).
    """
    y = (k - m) / sigma
    z = np.sqrt(y * y + 1.0)
    design = np.column_stack([np.ones_like(y), y, z])
    root_w = np.sqrt(weight)
    a_w = design * root_w[:, None]
    b_w = w * root_w

    def cost(p: np.ndarray) -> float:
        r = a_w @ p - b_w
        return float(r @ r)

    def jac(p: np.ndarray) -> np.ndarray:
        return 2.0 * (a_w.T @ (a_w @ p - b_w))

    start, *_ = np.linalg.lstsq(a_w, b_w, rcond=None)
    feasible = (
        start[2] >= abs(start[1]) - 1e-12
        and start[0] + np.sqrt(max(start[2] ** 2 - start[1] ** 2, 0.0)) >= -1e-12
    )
    # The unconstrained solution is usually already a valid slice, and skipping
    # the solver in that case is what keeps the 2-D outer search affordable.
    if feasible:
        return cost(start), start

    scale = max(float(w.max()), 1e-6)
    bounds = [(-2.0 * scale, 2.0 * scale), (-4.0 * scale, 4.0 * scale), (0.0, 4.0 * scale)]
    constraints = [
        {"type": "ineq", "fun": lambda p: p[2] - abs(p[1])},
        {"type": "ineq",
         "fun": lambda p: p[0] + np.sqrt(max(p[2] ** 2 - p[1] ** 2, 0.0))},
    ]
    out = minimize(
        cost, np.clip(start, [b[0] for b in bounds], [b[1] for b in bounds]),
        jac=jac, method="SLSQP", bounds=bounds, constraints=constraints,
        options={"maxiter": 200, "ftol": 1e-14},
    )
    return cost(out.x), out.x


def _to_raw(coeffs: np.ndarray, m: float, sigma: float, t: float) -> SVIParams:
    """Map the reduced coefficients back to raw SVI parameters."""
    a, c, d = float(coeffs[0]), float(coeffs[1]), float(coeffs[2])
    b = d / sigma
    rho = 0.0 if d <= 1e-14 else float(np.clip(c / d, -0.9999, 0.9999))
    return SVIParams(a=a, b=max(b, 0.0), rho=rho, m=float(m), sigma=float(sigma), t=t)


def fit_quasi_explicit(
    k: np.ndarray, w: np.ndarray, weight: np.ndarray, t: float
) -> tuple[SVIParams, float]:
    """Outer search over (m, sigma) with the exact inner solve."""
    span = max(float(k.max() - k.min()), 0.1)
    # m is bounded to the quoted range. Allowed outside it, the outer search
    # finds a degenerate fit whose vertex sits beyond the data: over the quoted
    # strikes only one branch of the hyperbola is visible, w comes out almost
    # linear, and the smile's curvature is lost. Those solutions scored better
    # on raw least squares while putting rho at +0.7 - the wrong sign for an
    # equity index - and left hundreds of basis points of error.
    m_lo, m_hi = float(k.min()), float(k.max())
    m_grid = np.linspace(m_lo, m_hi, 25)
    sigma_grid = np.exp(np.linspace(np.log(0.02), np.log(1.2), 25))

    best = (np.inf, None, None, None)
    for m in m_grid:
        for sigma in sigma_grid:
            cost, coeffs = _inner_linear_fit(k, w, weight, float(m), float(sigma))
            if cost < best[0]:
                best = (cost, coeffs, float(m), float(sigma))

    # Refine the two outer parameters around the best grid point.
    def outer(theta: np.ndarray) -> float:
        m, sigma = float(theta[0]), float(abs(theta[1]))
        if not (1e-3 <= sigma <= 3.0):
            return 1e12
        return _inner_linear_fit(k, w, weight, m, sigma)[0]

    def outer_bounded(theta: np.ndarray) -> float:
        if not (m_lo - 1e-9 <= theta[0] <= m_hi + 1e-9):
            return 1e12
        return outer(theta)

    refined = minimize(
        outer_bounded, np.array([best[2], best[3]]), method="Nelder-Mead",
        options={"maxiter": 600, "xatol": 1e-6, "fatol": 1e-14},
    )
    if refined.fun < best[0]:
        m, sigma = float(refined.x[0]), float(abs(refined.x[1]))
        cost, coeffs = _inner_linear_fit(k, w, weight, m, sigma)
        best = (cost, coeffs, m, sigma)

    return _to_raw(best[1], best[2], best[3], t), float(best[0])


def _is_admissible(params: SVIParams, tol: float = -1e-12) -> bool:
    """Can this slice's total variance go negative anywhere?

    The minimum of w over k is a + b*sigma*sqrt(1-rho^2), so requiring that to
    be non-negative is exactly the condition for a slice to describe a real
    implied-variance curve.
    """
    floor = params.a + params.b * params.sigma * np.sqrt(max(1.0 - params.rho**2, 0.0))
    return bool(params.b >= 0.0 and abs(params.rho) < 1.0 and params.sigma > 0.0 and floor >= tol)


def _residuals(theta: np.ndarray, k: np.ndarray, w: np.ndarray, weight: np.ndarray, t: float) -> np.ndarray:
    p = SVIParams(*theta, t=t)
    return np.sqrt(weight) * (p.total_variance(k) - w)


def _bounds(k: np.ndarray, w: np.ndarray) -> tuple[list[float], list[float]]:
    span = max(k.max() - k.min(), 0.2)
    return (
        [-2.0 * w.max(), 1e-8, -0.999, k.min(), 1e-4],
        [2.0 * w.max(), 4.0 * max(w.max(), 1e-6), 0.999, k.max(), 2.0],
    )


def fit_slice(
    k: np.ndarray,
    w: np.ndarray,
    weight: np.ndarray,
    t: float,
    *,
    enforce_butterfly: bool = True,
    grid: np.ndarray | None = None,
    floor_grid: np.ndarray | None = None,
    floor_values: np.ndarray | None = None,
    feasible_start: SVIParams | None = None,
) -> tuple[SVIParams, dict]:
    """Weighted least-squares fit of one slice, made arbitrage-free if needed.

    `grid` is where the butterfly condition is checked and, if needed, enforced.
    `floor_grid`/`floor_values` impose the calendar condition against the
    previous expiry, over the strikes the two expiries have in common.

    The fit runs from several starts because the raw-SVI objective has local
    minima: m and rho trade off against each other, and a single start from a
    bad guess lands on a smile that is visibly wrong in one wing.
    """
    lo, hi = _bounds(k, w)
    atm = float(np.interp(0.0, k, w))
    if grid is None:
        grid = slice_grid(k, atm)

    # Residuals are measured in total variance, but what matters is error in
    # implied vol. Those are not the same objective: on the SPX expiry at
    # T = 0.94, total variance runs from 0.016 at the money to 0.252 in the deep
    # put wing, so a near-uniform least-squares fit in w is dominated 250 to 1
    # by the wing and will happily leave 25 bp of error at the money. Since
    # dw = 2*sigma*T*dsigma, scaling each weight by 1/(2*sigma*T)^2 turns the
    # objective into squared vol error while keeping it linear in the
    # coefficients, which is what the quasi-explicit solve needs.
    sigma_market = np.sqrt(np.maximum(w, 1e-12) / t)
    vol_metric = 1.0 / np.maximum(2.0 * sigma_market * t, 1e-8) ** 2
    weight = np.asarray(weight, dtype=float) * vol_metric
    starts = []
    for b0 in (0.1 * atm + 1e-4, 0.4 * atm + 1e-4, 1.0 * atm + 1e-4):
        for rho0 in (-0.7, -0.3, 0.0):
            for m0 in (0.0, float(np.mean(k))):
                starts.append([0.5 * atm, b0, rho0, m0, 0.1])

    best, best_cost = None, np.inf
    for theta0 in starts:
        theta0 = np.clip(theta0, lo, hi)
        try:
            res = least_squares(
                _residuals, theta0, args=(k, w, weight, t),
                bounds=(lo, hi), max_nfev=4000, method="trf",
            )
        except Exception:
            continue
        if res.cost < best_cost:
            best, best_cost = res.x, res.cost

    # The quasi-explicit route is the primary fit; the multi-start nonlinear run
    # is kept because on a handful of slices it still wins, and taking the
    # better of the two costs almost nothing.
    #
    # Careful with scales: scipy's least_squares reports 0.5*||r||^2 while the
    # inner linear solve reports ||r||^2. Both are converted to ||r||^2 here
    # before being compared.
    nonlinear_sse = 2.0 * best_cost if best is not None else np.inf
    # A slice is only admissible if its total variance cannot go negative, i.e.
    # a + b*sigma*sqrt(1-rho^2) >= 0. The quasi-explicit solve imposes that
    # directly, but the multi-start nonlinear run only has box bounds on each
    # parameter and can return a slice that violates it: on the SPX expiry at
    # 5.2 years it produced a minimum total variance of -0.041, which means a
    # negative implied variance at some strike and a NaN local vol downstream.
    # So an inadmissible nonlinear fit is discarded however well it scores.
    if best is not None and not _is_admissible(SVIParams(*best, t=t)):
        nonlinear_sse = np.inf

    qe_params, qe_sse = fit_quasi_explicit(k, w, weight, t)
    if qe_sse <= nonlinear_sse:
        best, best_sse = np.array(qe_params.as_tuple()), qe_sse
    else:
        best_sse = nonlinear_sse

    if best is None:
        raise RuntimeError("SVI fit failed from every start")
    best_cost = best_sse

    params = SVIParams(*best, t=t)
    unconstrained = params
    info = {
        "rmse_vol": _rmse_vol(params, k, w, t),
        "rmse_vol_unconstrained": _rmse_vol(params, k, w, t),
        "butterfly_min": float(butterfly_g(params, grid).min()),
        "constrained": False,
        "cost": float(best_cost),
    }

    has_floor = floor_grid is not None and floor_values is not None and len(floor_grid) > 0
    needs_butterfly = enforce_butterfly and info["butterfly_min"] < BUTTERFLY_TOL
    needs_floor = has_floor and bool(
        np.any(params.total_variance(floor_grid) - floor_values < CALENDAR_TOL)
    )
    if not (needs_butterfly or needs_floor):
        return params, info

    # Refit under the violated condition. SLSQP is given the unconstrained fit
    # as its start, so the constrained answer stays as close to the quotes as
    # the no-arbitrage region allows.
    constraints = []
    if needs_butterfly:
        constraints.append(
            {"type": "ineq", "fun": lambda th: butterfly_g(SVIParams(*th, t=t), grid)}
        )
    if needs_floor:
        constraints.append(
            {"type": "ineq",
             "fun": lambda th: SVIParams(*th, t=t).total_variance(floor_grid) - floor_values}
        )

    def objective(th: np.ndarray) -> float:
        return float(np.sum(_residuals(th, k, w, weight, t) ** 2))

    def check(candidate: SVIParams) -> bool:
        return (
            _is_admissible(candidate)
            and (not needs_butterfly
                 or float(butterfly_g(candidate, grid).min()) >= BUTTERFLY_TOL)
            and (not needs_floor
                 or float(np.min(candidate.total_variance(floor_grid) - floor_values)) >= CALENDAR_TOL)
        )

    # Two starts. The unconstrained fit is closest to the quotes, but when the
    # calendar floor binds hard it can sit outside the feasible region and
    # SLSQP stalls. `feasible_start` is the previous expiry's slice scaled up to
    # this maturity, which satisfies the floor by construction because total
    # variance scales by t_new/t_prev > 1. Starting from a point known to be
    # feasible is what fixed the three expiries each on SPX and DIA where the
    # refit previously gave up and left a calendar violation behind.
    starts_to_try = [best]
    if feasible_start is not None:
        starts_to_try.append(np.array(feasible_start.as_tuple()))

    candidate, out = None, None
    for start in starts_to_try:
        out = minimize(
            objective, np.clip(start, lo, hi), method="SLSQP", bounds=list(zip(lo, hi)),
            constraints=constraints, options={"maxiter": 800, "ftol": 1e-12},
        )
        trial = SVIParams(*out.x, t=t)
        if check(trial):
            candidate = trial
            break
    if candidate is None and feasible_start is not None:
        # Last resort: the scaled previous slice itself. It fits the quotes
        # worse but it is a valid, monotone slice, which the surface needs more
        # than it needs the extra accuracy.
        scaled = SVIParams(*feasible_start.as_tuple(), t=t)
        if check(scaled):
            candidate = scaled
    if candidate is None:
        candidate = SVIParams(*out.x, t=t)
    feasible = check(candidate)
    # Only take the constrained answer if it actually satisfies what the
    # unconstrained fit violated. A feasible-but-terrible SLSQP point is worse
    # than reporting the violation honestly, so the fallback keeps the
    # unconstrained fit and the audit records that the slice still fails.
    if feasible:
        params = candidate
        info.update(
            rmse_vol=_rmse_vol(params, k, w, t),
            butterfly_min=float(butterfly_g(params, grid).min()),
            constrained=True,
            cost=float(objective(out.x)),
        )
    else:
        params = unconstrained
        info["constraint_refit_failed"] = True
    return params, info


def _rmse_vol(p: SVIParams, k: np.ndarray, w: np.ndarray, t: float) -> float:
    """Fit error expressed in vol points, which is the readable unit."""
    model = np.sqrt(np.maximum(p.total_variance(k), 1e-12) / t)
    market = np.sqrt(np.maximum(w, 1e-12) / t)
    return float(np.sqrt(np.mean((model - market) ** 2)))


def fit_surface(slices, *, enforce_calendar: bool = True, calendar_band: tuple[float, float] | None = None) -> tuple[list[SVIParams], list[dict]]:
    """Fit every expiry in increasing maturity, carrying the calendar floor forward.

    Expiries are fitted shortest first so each one can be held above its
    predecessor on CHECK_GRID. Imposing the floor during the fit is what keeps
    the surface monotone in T; checking afterwards and shrugging would leave a
    calendar spread priced negative somewhere in the wings.
    """
    params_out: list[SVIParams] = []
    report: list[dict] = []
    grids: list[np.ndarray] = []
    previous: SVIParams | None = None
    previous_grid: np.ndarray | None = None

    for sl in slices:
        atm = float(np.interp(0.0, sl.log_moneyness, sl.total_variance))
        grid = slice_grid(sl.log_moneyness, atm)

        # The calendar condition is imposed only where this expiry and the one
        # before it both have quotes. Outside the overlap neither slice carries
        # information, and holding the later one above the earlier one's
        # extrapolated wing is what wrecked the fits.
        floor_grid = floor_values = None
        scaled_previous = None
        if previous is not None:
            factor = sl.t / previous.t
            scaled_previous = SVIParams(
                a=previous.a * factor, b=previous.b * factor, rho=previous.rho,
                m=previous.m, sigma=previous.sigma, t=sl.t,
            )
        if enforce_calendar and previous is not None and previous_grid is not None:
            if calendar_band is not None:
                # Enforce across the union of the two slices' own grids, held
                # inside the calibrated band with the same padding the grids
                # use. Two earlier choices were both wrong. Scoping the floor to
                # each pair's quoted *overlap* left w non-monotone everywhere
                # else, and Dupire's numerator is dw/dT, so on the Dow surface
                # local variance went to zero over a fifth of the grid. Using
                # one fixed band instead left a gap the other way: slice grids
                # are padded past k_clip, so the audit sampled strikes the
                # constraint had never been applied at and found dips of 4e-4.
                # The union is the region the condition is actually checked on.
                pad_lo, pad_hi = calendar_band[0] - GRID_PAD, calendar_band[1] + GRID_PAD
                lo = max(min(grid[0], previous_grid[0]), pad_lo)
                hi = min(max(grid[-1], previous_grid[-1]), pad_hi)
            else:
                lo = max(grid[0], previous_grid[0])
                hi = min(grid[-1], previous_grid[-1])
            if hi > lo:
                floor_grid = np.linspace(lo, hi, FLOOR_GRID_POINTS)
                floor_values = previous.total_variance(floor_grid) + CALENDAR_MARGIN

        params, info = fit_slice(
            sl.log_moneyness, sl.total_variance, sl.weight, sl.t,
            grid=grid, floor_grid=floor_grid, floor_values=floor_values,
            feasible_start=scaled_previous,
        )
        info.update(expiry=sl.expiry, t=sl.t, n_points=len(sl.implied_vol),
                    forward=sl.forward, discount_factor=sl.discount_factor,
                    parity_rmse=sl.parity_rmse, k_min=float(sl.log_moneyness.min()),
                    k_max=float(sl.log_moneyness.max()))
        info["calendar_min_gap"] = (
            float("nan") if floor_grid is None
            else float(np.min(params.total_variance(floor_grid) - floor_values))
        )
        params_out.append(params)
        grids.append(grid)
        report.append(info)
        previous, previous_grid = params, grid

    for info, grid in zip(report, grids):
        info["grid_k_min"], info["grid_k_max"] = float(grid[0]), float(grid[-1])
    return params_out, report


def audit(params: list[SVIParams], report: list[dict] | None = None) -> dict:
    """Surface-wide arbitrage audit, for the validation table in the report.

    Each slice is audited on its own grid, matching where the fit was enforced.
    """
    if report is None:
        grids = [CHECK_GRID] * len(params)
    else:
        grids = [np.linspace(r["grid_k_min"], r["grid_k_max"], 161) for r in report]

    butterflies = [float(butterfly_g(p, g).min()) for p, g in zip(params, grids)]
    densities = [float(risk_neutral_density(p, g).min()) for p, g in zip(params, grids)]
    gaps = []
    for (earlier, g_e), (later, g_l) in zip(zip(params, grids), zip(params[1:], grids[1:])):
        # Union, matching where fit_surface imposes the condition. Checking a
        # region the constraint was never applied to measures the grid, not the
        # surface.
        lo, hi = min(g_e[0], g_l[0]), max(g_e[-1], g_l[-1])
        if hi <= lo:
            continue
        common = np.linspace(lo, hi, 241)
        gaps.append(float(np.min(later.total_variance(common) - earlier.total_variance(common))))
    return {
        "n_slices": len(params),
        "butterfly_min": min(butterflies) if butterflies else float("nan"),
        "butterfly_failures": int(sum(1 for g in butterflies if g < BUTTERFLY_TOL)),
        "density_min": min(densities) if densities else float("nan"),
        "calendar_min_gap": min(gaps) if gaps else float("nan"),
        "calendar_failures": int(sum(1 for g in gaps if g < CALENDAR_TOL)),
    }
