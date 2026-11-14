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


def test_figure_frame_ticks_and_tags():
    from app.figures.render import render_figure, to_pdf
    c = _genotype_table()
    base = render_figure(c, {"type": "bar", "group_col": "g", "value_col": "b"})["svg"]
    closed = render_figure(c, {"type": "bar", "group_col": "g", "value_col": "b", "frame": "closed", "minor_ticks": 1,
                               "tick_direction": "in", "y_tick_step": 0.25, "title": "T", "title_dx": 10})["svg"]
    assert 'data-el="frame"' in closed and 'data-el="frame"' not in base
    assert closed.count("<line") > base.count("<line")
    assert 'data-el="group"' in base and 'data-row="r0"' in base and 'data-el="y-axis"' in base
    assert ">0.25<" in closed
    assert to_pdf(closed).startswith(b"%PDF")
    xy = render_figure(c, {"type": "xy", "x": "a", "y": ["b"], "color_by": "g", "legend_dx": -20, "legend_dy": 10, "x_tick_step": 0.5})["svg"]
    assert 'data-el="legend"' in xy and 'data-x=' in xy


def test_p_adjust_matches_r():
    from app.stats.analyses import p_adjust
    p = [0.01, 0.04, 0.03, 0.005, 0.2, 0.0004]
    # R: p.adjust(p, "holm") and "bonferroni"
    assert [round(x, 10) for x in p_adjust(p, "holm")] == [0.04, 0.09, 0.09, 0.025, 0.2, 0.0024]
    assert [round(x, 10) for x in p_adjust(p, "bonferroni")] == [0.06, 0.24, 0.18, 0.03, 1.0, 0.0024]


def test_pairwise_and_auto_stars():
    from app.figures.render import render_figure, stars
    c = _genotype_table()
    r = run_analysis("pairwise", c, {"group_col": "g", "value_col": "b", "test": "welch", "correction": "holm", "mode": "all"})
    assert r["status"] == "ok" and len(r["comparisons"]) == 3
    single = run_analysis("ttest", c, {"group_col": "g", "value_col": "b", "groups": ["Col", "tga1"], "variant": "welch"})
    cmp0 = r["comparisons"][0]
    assert cmp0["p_raw"] == single["test"]["p"] and cmp0["p"] >= cmp0["p_raw"]
    r2 = run_analysis("pairwise", c, {"group_col": "g", "value_col": "b", "mode": "control", "control": "tga4"})
    assert [x["group1"] for x in r2["comparisons"]] == ["tga4", "tga4"]
    assert stars(0.04) == "*" and stars(0.00005) == "****" and stars(0.2) == "ns" and stars(0.03, [0.1, 0.05]) == "**"
    out = render_figure(c, {"type": "scatter_groups", "group_col": "g", "value_col": "b",
                            "annotations": [{"type": "pvalue", "analysis_id": "A", "all": True, "format": "stars"}]},
                        {"A": {"result": r, "stale": False}})
    assert out["svg"].count('class="pvalue-bracket"') == 3
    assert "Holm" in out["caption"] and "* p < 0.05" in out["caption"]


def _four_groups():
    import random
    rnd = random.Random(1)
    rows, i = [], 0
    for g, m in {"Col": 6.6, "tga1": 7.6, "tga4": 6.5, "tga1tga4": 8.3}.items():
        for _ in range(10):
            rows.append({"id": f"r{i}", "cells": {"g": g, "a": round(rnd.gauss(m, 0.35), 2)}})
            i += 1
    return {"columns": [{"id": "g", "name": "Genotype", "type": "text"}, {"id": "a", "name": "N%", "type": "numeric"}],
            "rows": rows, "design": {"layout": "wide", "group_columns": []}, "exclusions": []}


import pytest  # noqa: E402


@pytest.mark.parametrize("extra", [{}, {"y_max": 9}, {"y_max": 7}, {"y_scale": "log10"}, {"height_mm": 45}, {"frame": "closed", "font_size_pt": 11}])
def test_star_brackets_stay_inside_figure_and_below_title(extra):
    import re
    from app.figures.render import render_figure
    c = _four_groups()
    res = run_analysis("pairwise", c, {"group_col": "g", "value_col": "a", "test": "student", "correction": "none"})
    cfg = {"type": "box", "group_col": "g", "value_col": "a", "title": "Yo this is great", "show_points": True,
           "annotations": [{"type": "pvalue", "analysis_id": "A", "all": True, "format": "stars"}], **extra}
    out = render_figure(c, cfg, results={"A": {"result": res, "stale": False}})
    svg = out["svg"]
    title_size = 9.0
    title_bottom = 4 + title_size + 1
    groups = re.findall(r'<g data-el="pvalue"[^>]*>(.*?)</g>', svg, flags=re.S)
    assert len(groups) == 6  # all 6 pairs, ns included
    ys = []
    for g in groups:
        ys += [float(v) for v in re.findall(r'[ML][\d.]+,([\d.]+)', g)]
        ys += [float(v) - 8 for v in re.findall(r'<text[^>]* y="([\d.]+)"', g)]  # top of the glyphs (approx.)
    assert min(ys) >= title_bottom, (extra, min(ys))
    assert ">ns<" in svg
    # the caption names the test and the correction in words
    assert "Student" in out["caption"] and "correction" in out["caption"]


def test_pairwise_caption_holm_default():
    from app.figures.render import render_figure
    c = _four_groups()
    res = run_analysis("pairwise", c, {"group_col": "g", "value_col": "a"})
    out = render_figure(c, {"type": "box", "group_col": "g", "value_col": "a",
                            "annotations": [{"type": "pvalue", "analysis_id": "A", "all": True, "format": "stars"}]},
                        results={"A": {"result": res, "stale": False}}, lang="fr")
    assert "Welch" in out["caption"] and "Holm" in out["caption"]
