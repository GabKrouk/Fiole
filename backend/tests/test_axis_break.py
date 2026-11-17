"""Broken axes (Prism-like): a hidden range, // marks, two scale segments, data cut at the gap."""
import re

from app.figures.render import Scale, render_figure, to_pdf, to_png


def _table():
    # one group far above the others: the classic reason to break an axis
    rows = [("Ctrl", 4.1), ("Ctrl", 5.0), ("Ctrl", 4.6), ("Mut", 6.2), ("Mut", 7.1), ("Mut", 6.6), ("OE", 410.0), ("OE", 452.0), ("OE", 431.0)]
    return {
        "columns": [{"id": "g", "name": "Genotype", "type": "text"}, {"id": "v", "name": "Expression", "type": "numeric"}],
        "rows": [{"id": f"r{i}", "cells": {"g": g, "v": v}} for i, (g, v) in enumerate(rows)],
        "design": {"layout": "long", "group_col": "g", "value_col": "v"},
        "exclusions": [],
    }


def _xy():
    rows = []
    for i, (x, y) in enumerate([(0, 1), (1, 2), (2, 3), (3, 4), (100, 200), (101, 205), (102, 210)]):
        rows.append({"id": f"r{i}", "cells": {"x": x, "y": y, "g": "A"}})
        rows.append({"id": f"s{i}", "cells": {"x": x, "y": y * 1.3, "g": "B"}})
    return {"columns": [{"id": "x", "name": "Time", "type": "numeric"}, {"id": "y", "name": "Signal", "type": "numeric"},
                        {"id": "g", "name": "Line", "type": "text"}],
            "rows": rows, "design": {"layout": "long"}, "exclusions": []}


def test_scale_segments_and_gap():
    s = Scale(0, 100, 200, 0, brk=(40, 60))  # same scale on both sides: 40 units each
    pa, pb = s.gap()
    assert s(0) == 200 and s(100) == 0
    assert abs((200 - pa) - (pb - 0)) < 1e-6  # both segments the same length
    assert abs(pa - pb) == 6.0
    assert s.hidden(50) and not s.hidden(65)
    sq = Scale(0, 100, 200, 0, brk=(5, 50))  # 5 vs 50 units: the short segment still gets 30 %
    assert abs((200 - sq.gap()[0]) - 194 * 0.3) < 1e-6
    s2 = Scale(0, 100, 200, 0, brk=(10, 90), frac=0.25)
    assert abs((200 - s2.gap()[0]) - (200 - 6) * 0.25) < 1e-6
    assert Scale(0, 100, 200, 0, brk=(10, 120)).gap() is None  # break past the axis end: no break


def test_bar_with_y_break_has_marks_ticks_and_cut_bars():
    c = _table()
    base = {"type": "bar", "group_col": "g", "value_col": "v"}
    plain = render_figure(c, base)
    out = render_figure(c, {**base, "y_break_from": 10, "y_break_to": 380})
    svg = out["svg"]
    # the clip path has two bands (below and above the gap)
    clip = re.search(r'<clipPath id="plotclip">(.*?)</clipPath>', svg).group(1)
    assert clip.count("<rect") == 2
    # ticks of both segments are labelled, none inside the hidden range
    labels = [float(t.replace("−", "-")) for t in re.findall(r'>(−?[\d.]+)</text>', svg.split('data-el="y-axis"')[1].split("</g>")[0])]
    assert any(v <= 10 for v in labels) and any(v >= 380 for v in labels)
    assert not any(10 < v < 380 for v in labels)
    # the y axis line is split in two + 2 slashes
    yaxis = svg.split('data-el="y-axis"')[1].split("</g>")[0]
    assert yaxis.count("<line") >= plain["svg"].split('data-el="y-axis"')[1].split("</g>")[0].count("<line") + 3
    assert not out["warnings"]
    assert to_pdf(svg).startswith(b"%PDF") and to_png(svg, 150)[:4] == b"\x89PNG"


def test_break_outside_range_is_ignored_with_warning():
    c = _table()
    out = render_figure(c, {"type": "bar", "group_col": "g", "value_col": "v", "y_break_from": 400, "y_break_to": 5000})
    assert any("coupure" in w["fr"] for w in out["warnings"])
    clip = re.search(r'<clipPath id="plotclip">(.*?)</clipPath>', out["svg"]).group(1)
    assert clip.count("<rect") == 1


def test_values_in_break_warn():
    c = _table()
    out = render_figure(c, {"type": "scatter_groups", "group_col": "g", "value_col": "v", "y_break_from": 5.5, "y_break_to": 300})
    assert any("valeur(s) tombent" in w["fr"] for w in out["warnings"])


def test_xy_x_and_y_break_and_closed_frame():
    c = _xy()
    for t in ("xy", "line"):
        out = render_figure(c, {"type": t, "x": "x", "y": ["y"], "color_by": "g", "frame": "closed",
                                "x_break_from": 5, "x_break_to": 95, "y_break_from": 6, "y_break_to": 180, "x_stars": t == "line"})
        svg = out["svg"]
        clip = re.search(r'<clipPath id="plotclip">(.*?)</clipPath>', svg).group(1)
        assert clip.count("<rect") == 4
        assert not [w for w in out["warnings"] if "coupure" in w["fr"]]
        assert to_pdf(svg).startswith(b"%PDF")


def test_stars_with_y_break_stay_clear_of_the_frame():
    c = _table()
    # brackets come from saved analysis results: emulate one
    res = {"a1": {"result": {"status": "ok", "comparisons": [
        {"group1": "Ctrl", "group2": "OE", "p": 0.0001, "p_display": "0.0001"},
        {"group1": "Ctrl", "group2": "Mut", "p": 0.02, "p_display": "0.02"}]}, "stale": False}}
    cfg = {"type": "bar", "group_col": "g", "value_col": "v", "y_break_from": 10, "y_break_to": 380,
           "annotations": [{"type": "pvalue", "analysis_id": "a1", "all": True}]}
    out = render_figure(c, cfg, res)
    svg = out["svg"]
    top = float(re.search(r'data-el="plot" x="[\d.]+" y="([\d.]+)"', svg).group(1))
    ys = [float(v) for v in re.findall(r'<text x="[\d.]+" y="([\d.]+)"[^>]*class="pvalue-label"', svg)]
    assert ys and min(ys) > top - 1
