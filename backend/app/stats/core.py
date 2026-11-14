"""Shared statistical helpers."""
from __future__ import annotations

import math

import numpy as np
from scipy import optimize, stats

P_FLOOR = 2.2e-16  # same display floor as R (machine epsilon)


def format_p(p: float | None) -> str:
    """Never display p = 0. Below the floor, show '< 2.2e-16'."""
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "—"
    if p < P_FLOOR:
        return "< 2.2e-16"
    if p < 0.001:
        return f"{p:.2e}"
    if p < 0.1:
        return f"{p:.3g}"
    return f"{p:.3f}"


def clean(x):
    """Make values JSON-safe: NaN/inf → None, numpy scalars → Python."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return f if math.isfinite(f) else None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def describe(values: list[float], ci_level: float = 0.95) -> dict:
    a = np.asarray(values, dtype=float)
    n = int(a.size)
    out: dict = {"n": n}
    if n == 0:
        return out
    mean = float(a.mean())
    out.update(
        mean=mean,
        median=float(np.median(a)),
        min=float(a.min()),
        max=float(a.max()),
        q1=float(np.quantile(a, 0.25)),  # R type 7
        q3=float(np.quantile(a, 0.75)),
        sum=float(a.sum()),
    )
    if n >= 2:
        sd = float(a.std(ddof=1))
        sem = sd / math.sqrt(n)
        tcrit = float(stats.t.ppf(0.5 + ci_level / 2, n - 1))
        out.update(
            sd=sd,
            variance=sd**2,
            sem=sem,
            ci_low=mean - tcrit * sem,
            ci_high=mean + tcrit * sem,
            ci_level=ci_level,
            cv_percent=(sd / abs(mean) * 100) if mean != 0 else None,
            iqr=out["q3"] - out["q1"],
        )
    return out


def shapiro(values: list[float]) -> dict | None:
    if len(values) < 3 or len(values) > 5000 or np.ptp(values) == 0:
        return None
    w, p = stats.shapiro(values)
    return {"statistic_name": "W", "statistic": float(w), "p": float(p), "p_display": format_p(float(p))}


def brown_forsythe(a: list[float], b: list[float]) -> dict | None:
    if len(a) < 2 or len(b) < 2:
        return None
    if np.ptp(a) == 0 and np.ptp(b) == 0:
        return None
    f, p = stats.levene(a, b, center="median")
    return {"statistic_name": "F", "statistic": float(f), "df": [1, len(a) + len(b) - 2], "p": float(p), "p_display": format_p(float(p))}


def nct_ci(t_obs: float, df: float, level: float = 0.95) -> tuple[float, float] | None:
    """CI for the noncentrality parameter of a t statistic (pivoting the noncentral t CDF)."""
    alpha = 1 - level
    if not math.isfinite(t_obs) or df <= 0:
        return None

    def f(ncp, target):
        return stats.nct.cdf(t_obs, df, ncp) - target

    def bracket(target):
        # walk outward from t_obs in unit steps; avoids regions where nct.cdf returns NaN
        a = b = t_obs
        for _ in range(200):
            fa = f(a, target)
            if fa > 0:
                break
            a -= 1.0
        else:
            return None
        for _ in range(200):
            fb = f(b, target)
            if fb < 0:
                break
            b += 1.0
        else:
            return None
        return optimize.brentq(f, a, b, args=(target,), xtol=1e-12)

    try:
        lo = bracket(1 - alpha / 2)
        hi = bracket(alpha / 2)
    except ValueError:
        return None
    if lo is None or hi is None:
        return None
    return lo, hi


def t_with_label(name_en: str, name_fr: str) -> dict:
    return {"en": name_en, "fr": name_fr}


def fmt(v: float | None, digits: int = 4) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v:.{digits}g}"
