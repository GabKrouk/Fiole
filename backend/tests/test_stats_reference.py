"""Validate the statistics engine against R 4.3 reference values.

Reference values are produced by tests/reference/make_reference.R (base R only).
"""
import json
import math
from pathlib import Path

import pytest

from app.stats import run_analysis
from app.stats.core import format_p

REF = json.loads((Path(__file__).parent / "reference" / "r_reference.json").read_text())

A = [0.7, -1.6, -0.2, -1.2, -0.1, 3.4, 3.7, 0.8, 0.0, 2.0]
B = [1.9, 0.8, 1.1, 0.1, -0.1, 4.4, 5.5, 1.6, 4.6, 3.4]
U = [12.1, 14.3, 9.8, 11.6, 13.9, 10.4, 15.2]
V = [16.0, 13.1, 17.4, 18.9, 15.5, 14.8, 19.7, 16.6]
P1 = [5.1, 6.3, 4.8, 7.2, 5.9, 6.6, 5.4, 6.9]
P2 = [5.9, 6.4, 6.1, 7.0, 7.3, 7.9, 6.0, 8.5]
X = list(range(1, 13))
Y = [2.3, 2.9, 4.1, 4.8, 6.2, 6.1, 7.9, 8.4, 9.0, 10.8, 11.1, 12.9]
Y2 = [3.1, 1.2, 4.4, 2.0, 6.5, 5.1, 3.3, 8.8, 7.0, 9.9, 6.1, 12.0]

TOL = 1e-9


def wide(a, b, names=("A", "B"), subject=True):
    n = max(len(a), len(b))
    cols = [{"id": "s", "name": "Subject", "type": "text"}, {"id": "a", "name": names[0], "type": "numeric"}, {"id": "b", "name": names[1], "type": "numeric"}]
    rows = []
    for i in range(n):
        rows.append({"id": f"r{i}", "cells": {"s": f"S{i + 1}", "a": a[i] if i < len(a) else None, "b": b[i] if i < len(b) else None}})
    return {"columns": cols, "rows": rows, "design": {"layout": "wide", "group_columns": ["a", "b"], "subject": "s" if subject else None}, "exclusions": []}


def xy(x, y):
    return {
        "columns": [{"id": "x", "name": "X", "type": "numeric"}, {"id": "y", "name": "Y", "type": "numeric"}],
        "rows": [{"id": f"r{i}", "cells": {"x": a, "y": b}} for i, (a, b) in enumerate(zip(x, y))],
        "design": {"layout": "wide"},
        "exclusions": [],
    }


def close(a, b, tol=TOL):
    if isinstance(b, float) and math.isinf(b):
        return a is None or (isinstance(a, float) and math.isinf(a))
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


@pytest.mark.parametrize(
    "key,a,b,config",
    [
        ("welch_sleep", A, B, {"variant": "welch"}),
        ("student_sleep", A, B, {"variant": "student"}),
        ("paired_sleep", A, B, {"variant": "paired"}),
        ("welch_uv_less", U, V, {"variant": "welch", "alternative": "less"}),
        ("student_uv_greater_90", U, V, {"variant": "student", "alternative": "greater", "ci_level": 0.9}),
    ],
)
def test_ttests(key, a, b, config):
    r = run_analysis("ttest", wide(a, b), config)
    ref = REF[key]
    assert r["status"] == "ok", r["errors"]
    assert close(r["test"]["statistic"], ref["statistic"])
    assert close(r["test"]["df"], ref["df"])
    assert close(r["test"]["p"], ref["p"])
    assert close(r["estimate"]["value"], ref["estimate"])
    assert close(r["estimate"]["ci"][0], ref["ci_low"])
    assert close(r["estimate"]["ci"][1], ref["ci_high"])


def test_cohen_d_ci():
    r = run_analysis("ttest", wide(A, B), {"variant": "student"})
    ref = REF["cohen_d_sleep"]
    es = r["effect_size"]
    assert close(es["value"], ref["value"])
    assert close(es["ci"][0], ref["ci_low"], 1e-7)
    assert close(es["ci"][1], ref["ci_high"], 1e-7)


def test_cohen_dz_ci():
    r = run_analysis("ttest", wide(A, B), {"variant": "paired"})
    ref = REF["cohen_dz_sleep"]
    es = r["effect_size"]
    assert close(es["value"], ref["value"])
    assert close(es["ci"][0], ref["ci_low"], 1e-7)
    assert close(es["ci"][1], ref["ci_high"], 1e-7)


@pytest.mark.parametrize(
    "key,kind,a,b,config,method",
    [
        ("mw_sleep_ties", "mannwhitney", A, B, {}, "normal"),
        ("mw_uv_exact", "mannwhitney", U, V, {}, "exact"),
        ("mw_uv_exact_greater", "mannwhitney", U, V, {"alternative": "greater"}, "exact"),
        ("wilcoxon_sleep_ties", "wilcoxon", A, B, {}, "normal"),
        ("wilcoxon_p_exact", "wilcoxon", P1, P2, {}, "exact"),
    ],
)
def test_rank_tests(key, kind, a, b, config, method):
    r = run_analysis(kind, wide(a, b), config)
    ref = REF[key]
    assert r["status"] == "ok", r["errors"]
    assert close(r["test"]["statistic"], ref["statistic"])
    assert close(r["test"]["p"], ref["p"], 1e-8)
    assert r["test"]["method"].startswith(method)


def test_descriptive():
    r = run_analysis("descriptive", wide(A, B), {})
    g = r["groups"][0]
    ref = REF["desc_a"]
    for k in ("mean", "sd", "median", "q1", "q3", "ci_low", "ci_high"):
        assert close(g[k], ref[k]), k


def test_pearson():
    r = run_analysis("correlation", xy(X, Y), {"x": "x", "y": "y", "method": "pearson"})
    ref = REF["pearson"]
    assert close(r["effect_size"]["value"], ref["r"])
    assert close(r["test"]["statistic"], ref["t"], 1e-8)
    assert close(r["test"]["p"], ref["p"], 1e-7)
    assert close(r["effect_size"]["ci"][0], ref["ci_low"], 1e-8)
    assert close(r["effect_size"]["ci"][1], ref["ci_high"], 1e-8)


def test_spearman():
    r = run_analysis("correlation", xy(X, Y2), {"x": "x", "y": "y", "method": "spearman"})
    ref = REF["spearman"]
    assert close(r["effect_size"]["value"], ref["rho"])
    assert close(r["test"]["p"], ref["p"], 1e-8)


def test_linear_regression():
    r = run_analysis("linear_regression", xy(X, Y), {"x": "x", "y": "y"})
    ref = REF["lm"]
    i, s = r["coefficients"]
    assert close(i["estimate"], ref["intercept"])
    assert close(s["estimate"], ref["slope"])
    assert close(i["se"], ref["se_int"])
    assert close(s["se"], ref["se_slope"])
    assert close(s["t"], ref["t_slope"], 1e-8)
    assert close(s["p"], ref["p_slope"], 1e-7)
    assert close(i["p"], ref["p_int"], 1e-8)
    assert close(r["fit"]["r_squared"], ref["r2"])
    assert close(r["fit"]["adj_r_squared"], ref["adj_r2"])
    assert close(r["fit"]["sigma"], ref["sigma"])
    assert close(r["fit"]["f"], ref["f"], 1e-8)
    assert close(s["ci"][0], ref["slope_ci_low"])
    assert close(s["ci"][1], ref["slope_ci_high"])
    assert close(i["ci"][0], ref["int_ci_low"])
    assert close(i["ci"][1], ref["int_ci_high"])


def test_shapiro_diagnostic():
    r = run_analysis("ttest", wide(A, B), {"variant": "welch"})
    ref = REF["shapiro_a"]
    sw = r["diagnostics"][0]
    assert close(sw["statistic"], ref["W"], 1e-6)
    assert close(sw["p"], ref["p"], 1e-4)


def test_tiny_p_never_zero():
    big1 = [float(v) for v in range(1, 101)]
    big2 = [v + 60 for v in big1]
    r = run_analysis("ttest", wide(big1, big2), {"variant": "student"})
    assert close(r["test"]["p"], REF["tiny_p"]["p"], 1e-6)
    assert r["test"]["p_display"] == "< 2.2e-16"
    assert format_p(0.0) == "< 2.2e-16"
    assert format_p(0.0312) == "0.0312"
    assert format_p(0.000123) == "1.23e-04"
