"""Edge cases: the engine must refuse or warn rather than invent a result."""
from app.stats import run_analysis
from tests.test_stats_reference import wide, xy


def codes(r, key="errors"):
    return [m["code"] for m in r[key]]


def test_missing_values_are_not_zeros():
    r = run_analysis("descriptive", wide([1.0, None, 3.0], [2.0, 2.0, 2.0]), {})
    g = r["groups"][0]
    assert g["n"] == 2 and g["mean"] == 2.0 and g["n_missing"] == 1


def test_n_too_small():
    r = run_analysis("ttest", wide([1.0], [2.0, 3.0]), {"variant": "welch"})
    assert r["status"] == "error" and "n_too_small" in codes(r)


def test_zero_variance_both():
    r = run_analysis("ttest", wide([1.0, 1.0, 1.0], [1.0, 1.0, 1.0]), {"variant": "welch"})
    assert "zero_variance_both" in codes(r)


def test_paired_constant_difference():
    r = run_analysis("ttest", wide([1.0, 2.0, 3.0], [2.0, 3.0, 4.0]), {"variant": "paired"})
    assert "zero_variance_diff" in codes(r)


def test_paired_unmatched_warns():
    r = run_analysis("ttest", wide([1.0, 2.0, 3.5, 4.0], [2.1, 2.9, None, 5.5]), {"variant": "paired"})
    assert r["status"] == "ok"
    assert "pairing_unmatched" in codes(r, "warnings")
    assert r["n_pairs"] == 3


def test_exclusions_are_respected_and_reported():
    content = wide([1.0, 2.0, 3.0, 100.0], [2.0, 3.0, 4.0, 5.0])
    content["exclusions"] = [{"row_id": "r3", "reason": "pipetting error"}]
    r = run_analysis("descriptive", content, {})
    assert r["groups"][0]["n"] == 3 and r["groups"][0]["n_excluded"] == 1


def test_technical_replicates_averaged():
    content = {
        "columns": [
            {"id": "g", "name": "Group", "type": "text"},
            {"id": "m", "name": "Mouse", "type": "text"},
            {"id": "v", "name": "Value", "type": "numeric"},
        ],
        "rows": [
            {"id": f"r{i}", "cells": {"g": g, "m": m, "v": v}}
            for i, (g, m, v) in enumerate(
                [("WT", "m1", 1.0), ("WT", "m1", 1.2), ("WT", "m2", 2.0), ("WT", "m2", 2.2), ("WT", "m3", 1.5),
                 ("KO", "m4", 3.0), ("KO", "m4", 3.4), ("KO", "m5", 2.8), ("KO", "m6", 3.9), ("KO", "m6", 4.1)]
            )
        ],
        "design": {"layout": "long", "group": "g", "value": "v", "subject": "m"},
        "exclusions": [],
    }
    r = run_analysis("ttest", content, {"variant": "welch", "average_technical": True, "groups": ["WT", "KO"]})
    assert r["status"] == "ok"
    assert [g["n"] for g in r["groups"]] == [3, 3]
    assert "technical_averaged" in codes(r, "warnings")


def test_regression_x_constant():
    r = run_analysis("linear_regression", xy([1, 1, 1, 1], [1, 2, 3, 4]), {"x": "x", "y": "y"})
    assert "x_zero_variance" in codes(r)


def test_unavailable_analysis():
    r = run_analysis("anova1", wide([1.0, 2.0], [3.0, 4.0]), {})
    assert r["status"] == "error" and "not_implemented" in codes(r)


def test_method_text_bilingual():
    r = run_analysis("ttest", wide([1.0, 2.0, 3.0, 4.0], [2.0, 3.5, 4.0, 6.0]), {"variant": "welch", "experimental_unit": "mouse"})
    assert "Welch" in r["method_text"]["en"] and "Welch" in r["method_text"]["fr"]
    assert "mouse" in r["method_text"]["en"]
