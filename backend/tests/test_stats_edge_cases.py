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


def _genotype_table():
    rows = [("Col", 6.76, 0.26), ("Col", 7.18, 0.69), ("Col", 6.95, 0.46), ("tga1", 7.64, 1.12), ("tga1", 7.50, 1.03),
            ("tga1", 7.13, 0.62), ("tga4", 7.08, 0.57), ("tga4", 8.13, 1.66), ("tga4", 7.19, 0.74)]
    return {
        "columns": [{"id": "g", "name": "Genotype", "type": "text"}, {"id": "a", "name": "pod", "type": "numeric"},
                    {"id": "b", "name": "Fresh weight", "type": "numeric"}],
        "rows": [{"id": f"r{i}", "cells": {"g": g, "a": a, "b": b}} for i, (g, a, b) in enumerate(rows)],
        # deliberately "wrong" saved layout: the column picks must override it
        "design": {"layout": "wide", "group_columns": ["a", "b"], "subject": "g"},
        "exclusions": [],
    }


def test_column_driven_groups_override_layout():
    c = _genotype_table()
    r = run_analysis("descriptive", c, {"group_col": "g", "value_col": "b"})
    assert [g["name"] for g in r["groups"]] == ["Col", "tga1", "tga4"]
    assert [g["n"] for g in r["groups"]] == [3, 3, 3]
    r = run_analysis("ttest", c, {"group_col": "g", "value_col": "a", "groups": ["Col", "tga4"], "variant": "welch"})
    assert r["status"] == "ok" and r["groups"][1]["name"] == "tga4"


def test_figure_column_driven_and_color_by():
    from app.figures.render import render_figure
    c = _genotype_table()
    out = render_figure(c, {"type": "box", "group_col": "g", "value_col": "b"})
    assert ">tga1<" in out["svg"] and ">Fresh weight<" in out["svg"] and ">Genotype<" in out["svg"]
    out = render_figure(c, {"type": "xy", "x": "a", "y": ["b"], "color_by": "g"})
    assert out["svg"].count("<path") >= 3 and ">tga4<" in out["svg"]  # one regression line + legend entry per genotype
