"""Figure gallery: ready-to-use figures guessed from the user's own table (columns, groups, repeated X),
each rendered with the real data so the user can simply click the one they want.

Nothing here is specific to a dataset: the roles of the columns are inferred from their content.
"""
from __future__ import annotations

import re

from ..data import _label, excluded_row_ids
from .render import close_x_clusters

TIME_RE = re.compile(r"\b(time|temps|t|jour|jours|day|days|h|hour|hours|heure|heures|min|minutes?|sec|dose|conc|concentration|dpi|dap|age|âge|stade|stage|week|semaine)\b", re.I)
STYLE_KEYS = ("font_family", "font_size_pt", "title_size_pt", "line_width_pt", "point_size_pt", "point_shape", "palette", "colors",
              "frame", "tick_direction", "tick_length_pt", "minor_ticks", "fill_opacity", "group_labels", "group_order")


def _columns(content: dict) -> tuple[list[dict], list[dict], dict[str, list[float]]]:
    excl = excluded_row_ids(content)
    rows = [r for r in content["rows"] if r["id"] not in excl]
    numeric, factors, values = [], [], {}
    for c in content["columns"]:
        vals = [r["cells"].get(c["id"]) for r in rows]
        present = [v for v in vals if v not in (None, "")]
        if not present:
            continue
        if c.get("type") == "text" or any(isinstance(v, str) for v in present):
            levels = []
            for v in present:
                s = _label(v)
                if s is not None and s not in levels:
                    levels.append(s)
            if 2 <= len(levels) <= 12 and len(levels) < len(present):
                factors.append({**c, "levels": levels})
        else:
            nums = [float(v) for v in present if isinstance(v, (int, float))]
            if len(nums) >= 3:
                numeric.append(c)
                values[c["id"]] = nums
    return numeric, factors, values


def _signal(content: dict, ycol: str, keys: list[str]) -> float:
    """Share of the variance of `ycol` explained by the cells of `keys` (e.g. time × treatment):
    the gallery leads with the variable where something visibly happens."""
    excl = excluded_row_ids(content)
    cells: dict[tuple, list[float]] = {}
    allv = []
    for r in content["rows"]:
        if r["id"] in excl:
            continue
        y = r["cells"].get(ycol)
        if not isinstance(y, (int, float)):
            continue
        k = tuple(_label(r["cells"].get(c)) for c in keys)
        cells.setdefault(k, []).append(float(y))
        allv.append(float(y))
    if len(allv) < 3:
        return 0.0
    m = sum(allv) / len(allv)
    tot = sum((v - m) ** 2 for v in allv)
    if tot == 0:
        return 0.0
    between = sum(len(vs) * (sum(vs) / len(vs) - m) ** 2 for vs in cells.values())
    return between / tot


ID_RE = re.compile(r"(échantillon|echantillon|sample|\bid\b|n°|\bno\b|num|#|plante|plant|individu|subject|sujet|rep)", re.I)


def _is_id(vals: list[float], name: str = "", first: bool = False) -> bool:
    """1, 2, 3 … n in the first column, or in a column named like one: a sample number, not a measurement."""
    if not (first or ID_RE.search(name or "")) or len(vals) < 6:
        return False
    if len(set(vals)) != len(vals) or any(v != int(v) for v in vals):
        return False
    # a few samples may be excluded or missing: 1…35 without 23 is still a numbering
    return max(vals) - min(vals) + 1 <= len(vals) * 1.25


def _x_like(c: dict, vals: list[float]) -> float:
    """Score of a column as the X of a time course / dose series: few distinct values, each repeated."""
    n, d = len(vals), len(close_x_clusters(vals))
    if d < 3 or d > 15 or d > n / 2:
        return 0.0
    return 1.0 + (2.0 if TIME_RE.search(c["name"] or "") else 0.0) + (1.0 - d / n)


def _sem_line(x: str, ys: list[str], color: str | None, nlev: int, vals: list[float]) -> dict:
    cfg = {"type": "line", "x": x, "y": ys, "center": "mean", "error": "sem", "show_points": True}
    if color:
        cfg["color_by"] = color
        if nlev >= 2:
            cfg["x_stars"] = True
    if any(len(cl) > 1 for cl in close_x_clusters(vals)):
        cfg["x_merge"] = True
    return cfg


def suggestions(content: dict) -> list[dict]:
    numeric, factors, values = _columns(content)
    first = content["columns"][0]["id"] if content["columns"] else None
    ids = {c["id"] for c in numeric if _is_id(values[c["id"]], c["name"], c["id"] == first)}
    meas = [c for c in numeric if c["id"] not in ids]
    color = factors[0] if factors else None
    nlev = len(color["levels"]) if color else 0
    out: list[dict] = []

    def add(key: str, fr: str, en: str, dfr: str, den: str, cfg: dict):
        out.append({"id": key, "title": {"fr": fr, "en": en}, "description": {"fr": dfr, "en": den}, "config": cfg})

    scored = sorted(((c, _x_like(c, values[c["id"]])) for c in meas), key=lambda t: -t[1])
    xcol = scored[0][0] if scored and scored[0][1] > 0 else None
    if xcol:
        x = xcol["id"]
        xv = values[x]
        ys = [c for c in meas if c["id"] != x]
        names = ", ".join(c["name"] for c in ys[:4]) + ("…" if len(ys) > 4 else "")
        cby = color["id"] if color else None
        if len(ys) >= 2:
            sel = [c["id"] for c in ys[:9]]
            ncol = len(sel) if len(sel) <= 3 else (2 if len(sel) == 4 else 3)
            nrow = -(-len(sel) // ncol)
            add("panels", "Toutes les variables, un panneau chacune", "All variables, one panel each",
                f"{xcol['name']} en X ; {names}. Moyenne ± SEM, points = réplicats" + (", étoiles à chaque X." if cby else "."),
                f"{xcol['name']} on X; {names}. Mean ± SEM, points = replicates" + (", stars at each X." if cby else "."),
                {**_sem_line(x, sel, cby, nlev, xv), "y_layout": "panels", "width_mm": 180, "height_mm": min(60 * nrow, 240),
                 # times like 20 … 1540 min: a log X axis spreads the early points instead of piling them up at 0
                 **({"x_scale": "log10"} if min(xv) > 0 and max(xv) / min(xv) >= 30 else {})})
        if ys:
            first = max(ys, key=lambda c: _signal(content, c["id"], [x] + ([cby] if cby else [])))
            add("line", f"Cinétique : {first['name']}", f"Time course: {first['name']}",
                "Moyenne ± SEM à chaque X, points = réplicats" + (f", une couleur par {color['name']}." if color else "."),
                "Mean ± SEM at each X, points = replicates" + (f", one colour per {color['name']}." if color else "."),
                {**_sem_line(x, [first["id"]], cby, nlev, xv), "width_mm": 85, "height_mm": 70})
            if min(xv) > 0 and min(values[first["id"]]) > 0:
                add("loglog", f"Log-log : {first['name']}", f"Log-log: {first['name']}",
                    "Mêmes données en échelle log sur les deux axes : des courbes parallèles = un facteur constant.",
                    "Same data with log scales on both axes: parallel curves = a constant factor.",
                    {**_sem_line(x, [first["id"]], cby, nlev, xv), "x_scale": "log10", "y_scale": "log10", "width_mm": 85, "height_mm": 70})
            if len(ys) >= 2 and not cby:
                sel = [c["id"] for c in ys[:4]]
                add("overlay", "Variables superposées", "Variables overlaid",
                    "Plusieurs variables sur le même axe Y (à réserver aux variables de même unité).",
                    "Several variables on one Y axis (best for variables with the same unit).",
                    {**_sem_line(x, sel, None, 0, xv), "width_mm": 85, "height_mm": 70})

    if color and meas:
        pool = [c for c in meas if not xcol or c["id"] != xcol["id"]] or meas
        v = max(pool, key=lambda c: _signal(content, c["id"], [color["id"]]))
        common = {"group_col": color["id"], "value_col": v["id"], "width_mm": 70, "height_mm": 70}
        add("bar", f"Barres : {v['name']} par {color['name']}", f"Bars: {v['name']} by {color['name']}",
            "Moyenne ± SEM et points individuels, étoiles de significativité.", "Mean ± SEM with individual points, significance stars.",
            {"type": "bar", **common, "error": "sem", "show_points": True, "annotations": [{"type": "pvalue", "all": True}]})
        add("dots", f"Points : {v['name']} par {color['name']}", f"Dot plot: {v['name']} by {color['name']}",
            "Chaque valeur, moyenne ± ET.", "Every value, mean ± SD.",
            {"type": "scatter_groups", **common, "error": "sd", "annotations": [{"type": "pvalue", "all": True}]})
        add("box", f"Boîtes : {v['name']} par {color['name']}", f"Box plot: {v['name']} by {color['name']}",
            "Médiane, quartiles et points individuels.", "Median, quartiles and individual points.",
            {"type": "box", **common, "show_points": True})
    if len(meas) >= 2:
        cand = [c for c in meas if not xcol or c["id"] != xcol["id"]]
        if len(cand) >= 2:
            a, b = cand[0], cand[1]
            cfg = {"type": "xy", "x": a["id"], "y": [b["id"]], "regression_line": True, "width_mm": 80, "height_mm": 70}
            if color:
                cfg["color_by"] = color["id"]
            add("xy", f"Nuage : {b['name']} selon {a['name']}", f"Scatter: {b['name']} vs {a['name']}",
                "Chaque observation, droite de régression" + (f", une couleur par {color['name']}." if color else "."),
                "Every observation, regression line" + (f", one colour per {color['name']}." if color else "."), cfg)
    return out
