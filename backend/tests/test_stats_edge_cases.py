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


@pytest.mark.parametrize("y_max", [None, 9])
def test_gab_scenario_one_bracket_per_pair_single_format_clear_of_frame(y_max):
    """4 groups, all pairs, plus older single-pair annotations on the same pair (one exact, one stars),
    closed frame: one bracket per pair, one label style, nothing crossing the frame or the title."""
    import re
    from app.figures.render import render_figure
    c = _four_groups()
    res = run_analysis("pairwise", c, {"group_col": "g", "value_col": "a"})
    single = run_analysis("ttest", c, {"group_col": "g", "value_col": "a", "groups": ["Col", "tga1"], "variant": "welch"})
    anns = [{"type": "pvalue", "analysis_id": "S", "format": "exact"},
            {"type": "pvalue", "analysis_id": "A", "format": "exact"},
            {"type": "pvalue", "analysis_id": "A", "all": True, "format": "stars"}]
    cfg = {"type": "box", "group_col": "g", "value_col": "a", "title": "Yo this is great", "frame": "closed",
           "show_points": True, "hide_ns": True, "y_min": 6 if y_max else None, "y_max": y_max, "annotations": anns}
    results = {"A": {"result": res, "stale": False}, "S": {"result": single, "stale": False}}
    out = render_figure(c, cfg, results=results)
    svg = out["svg"]
    groups = re.findall(r'<g data-el="pvalue"[^>]*>(.*?)</g>', svg, flags=re.S)
    labels = re.findall(r'class="pvalue-label"[^>]*>([^<]*)<', svg)
    n_sig = sum(1 for x in res["comparisons"] if x["p"] < 0.05)
    assert len(groups) == n_sig  # one bracket per significant pair, no duplicates
    assert all(set(l) == {"*"} for l in labels), labels  # stars only, never mixed with "p = …"
    # frame top line (closed frame) and title
    fr = re.search(r'<g data-el="frame">\s*<line x1="[\d.]+" y1="([\d.]+)"', svg)
    frame_top = float(fr.group(1))
    for g in groups:
        ys = [float(v) for v in re.findall(r'[ML][\d.]+,([\d.]+)', g)]
        ty = [float(v) for v in re.findall(r'<text[^>]* y="([\d.]+)"', g)]
        span = (min(ys + [t - 8 for t in ty]), max(ys + ty))
        inside = span[0] > frame_top + 0.5
        above = span[1] < frame_top - 0.5
        assert inside or above, (y_max, span, frame_top)  # never across the frame line
        assert span[0] >= 4 + 9 + 1  # below the title
    # global switch to exact p-values applies to every bracket
    out = render_figure(c, {**cfg, "p_format": "exact"}, results=results)
    labels = re.findall(r'class="pvalue-label"[^>]*>([^<]*)<', out["svg"])
    assert labels and all(l.startswith("p ") for l in labels), labels
    assert "* p &lt; 0.05" not in out["caption"] and "* p < 0.05" not in out["caption"]


STYLE_PRESETS = {  # mirrors the editor's quick styles (font sizes as applied at each size)
    "prism": lambda k: {"font_size_pt": round(10 * k * 2) / 2, "title_size_pt": round(12 * k * 2) / 2, "line_width_pt": 1, "tick_length_pt": 4},
    "journal": lambda k: {"font_size_pt": 7, "title_size_pt": 8, "line_width_pt": 0.5, "tick_length_pt": 2, "point_size_pt": 3},
    "boxed": lambda k: {"frame": "closed", "tick_direction": "in", "minor_ticks": 1},
    "poster": lambda k: {"font_size_pt": round(10 * k * 2) / 2, "title_size_pt": round(12 * k * 2) / 2, "line_width_pt": 1.5, "point_size_pt": 6},
    "minimal": lambda k: {"palette": "grayscale", "fill_opacity": 0.15, "line_width_pt": 0.5},
}


@pytest.mark.parametrize("style", list(STYLE_PRESETS))
@pytest.mark.parametrize("size", [(60, 50), (80, 70), (180, 100)])
@pytest.mark.parametrize("fmt,fixed", [("stars", False), ("exact", True)])
def test_quick_styles_render_cleanly_at_all_sizes(style, size, fmt, fixed):
    import math
    import re
    from app.figures.render import render_figure
    c = _four_groups()
    res = run_analysis("pairwise", c, {"group_col": "g", "value_col": "a"})
    w, h = size
    k = min(1.6, max(0.8, math.sqrt(w * h / 5600)))
    cfg = {"type": "box", "group_col": "g", "value_col": "a", "title": "Yo this is great", "show_points": True, "width_mm": w, "height_mm": h,
           "p_format": fmt, "annotations": [{"type": "pvalue", "analysis_id": "A", "all": True}], **STYLE_PRESETS[style](k)}
    if fixed:
        cfg.update(y_min=6, y_max=9)
    out = render_figure(c, cfg, results={"A": {"result": res, "stale": False}})
    svg = out["svg"]
    fs = cfg.get("font_size_pt", 8.0)
    ts = cfg.get("title_size_pt", 9.0)
    # brackets: below the title, inside the figure
    ys = []
    for g in re.findall(r'<g data-el="pvalue"[^>]*>(.*?)</g>', svg, flags=re.S):
        ys += [float(v) for v in re.findall(r'[ML][\d.]+,([\d.]+)', g)]
    assert ys and min(ys) > 4 + ts, (style, size, min(ys))
    # the order of the groups never changes
    labels = re.findall(r'<g data-el="group-label" data-group="([^"]+)"', svg)
    assert labels == ["Col", "tga1", "tga4", "tga1tga4"]
    # y tick labels never overlap
    yax = re.search(r'<g data-el="y-axis">(.*?)</g>', svg, flags=re.S).group(1)
    ty = sorted(float(v) for v in re.findall(r'<text[^>]* y="([\d.]+)"', yax))
    assert all(b - a >= fs * 1.2 for a, b in zip(ty, ty[1:])), (style, size, ty)
    # the data keep a real share of the figure: plot area >= 30 % of the figure height
    plot_h = float(re.search(r'<rect data-el="plot" x="[\d.]+" y="[\d.]+" width="[\d.]+" height="([\d.]+)"', svg).group(1))
    fig_h = h / 25.4 * 72
    too_small = any("too small" in w["en"] for w in out["warnings"])
    assert plot_h >= (0.2 if too_small else 0.3) * fig_h, (style, size, plot_h, fig_h)
    if too_small:  # only on the smallest panel
        assert size == (60, 50)


def test_rich_text_markup_parse_and_render():
    import re
    from app.figures import richtext as rt
    from app.figures.render import render_figure
    runs = rt.parse('Effect of <i>tga1</i> on N<sub>2</sub> <span color="#d00000"><b>x</b></span> p &lt; 0.05 <foo>')
    assert [(t, s.i, s.b, s.shift, s.color) for t, s in runs] == [
        ("Effect of ", False, False, "", None), ("tga1", True, False, "", None), (" on N", False, False, "", None),
        ("2", False, False, "sub", None), (" ", False, False, "", None), ("x", False, True, "", "#d00000"),
        (" p < 0.05 <foo>", False, False, "", None)]
    assert rt.plain("<i>tga1</i> &amp; <b>B</b>") == "tga1 & B"
    # real font metrics: italic/bold text measured, wider bold
    assert rt.measure("tga1", 10, bold=True) > rt.measure("tga1", 10)
    c = _four_groups()
    out = render_figure(c, {"type": "box", "group_col": "g", "value_col": "a", "title": "Effect of <i>tga1</i> <u>under</u>",
                            "group_labels": {"tga1": "<i>tga1</i>"}})
    svg = out["svg"]
    assert re.search(r'font-style="italic"[^>]*>tga1</text>', svg)
    assert svg.count('font-style="italic"') == 2
    assert "&lt;i&gt;" not in svg  # markup never leaks as literal text
    assert "tga1: n = 10" in out["caption"] and "<i>" not in out["caption"]
