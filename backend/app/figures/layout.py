"""Multi-panel layouts (like a Prism layout sheet): a page in mm, a grid of cells, one figure per cell,
automatic panel letters (A, B, C…). Each figure is rendered by the normal renderer at its cell size, then
placed with a translate: the page export (SVG / PDF / PNG) is as exact as a single figure's export."""
from __future__ import annotations

import re

from .render import MM

DEFAULT = {
    "width_mm": 180.0, "height_mm": 120.0, "rows": 1, "cols": 2, "gap_mm": 4.0, "margin_mm": 2.0,
    "col_widths": None, "row_heights": None,  # relative sizes; None = equal
    "label_show": True, "label_style": "A", "label_size_pt": 12.0, "label_bold": True, "label_font": "Arial, Helvetica, sans-serif",
    "background": "white", "panels": [],
}
STYLES = ("A", "a", "(A)", "(a)", "A.", "a.")


def merged(cfg: dict | None) -> dict:
    out = dict(DEFAULT)
    out.update({k: v for k, v in (cfg or {}).items() if v is not None})
    out["width_mm"] = float(min(max(float(out["width_mm"]), 30), 600))
    out["height_mm"] = float(min(max(float(out["height_mm"]), 30), 600))
    out["rows"] = int(min(max(int(out["rows"]), 1), 6))
    out["cols"] = int(min(max(int(out["cols"]), 1), 6))
    out["gap_mm"] = float(min(max(float(out["gap_mm"]), 0), 40))
    out["margin_mm"] = float(min(max(float(out["margin_mm"]), 0), 40))
    if out["label_style"] not in STYLES:
        out["label_style"] = "A"
    return out


def _fractions(vals, n: int) -> list[float]:
    if not isinstance(vals, list) or len(vals) != n or any(not isinstance(v, (int, float)) or v <= 0 for v in vals):
        return [1.0 / n] * n
    s = float(sum(vals))
    return [v / s for v in vals]


def cells(cfg: dict) -> list[dict]:
    """Cell rectangles in mm, row-major: [{index, row, col, x, y, w, h}]."""
    rows, cols, gap, m = cfg["rows"], cfg["cols"], cfg["gap_mm"], cfg["margin_mm"]
    aw = cfg["width_mm"] - 2 * m - gap * (cols - 1)
    ah = cfg["height_mm"] - 2 * m - gap * (rows - 1)
    cw = [f * aw for f in _fractions(cfg.get("col_widths"), cols)]
    rh = [f * ah for f in _fractions(cfg.get("row_heights"), rows)]
    out = []
    y = m
    for r in range(rows):
        x = m
        for c in range(cols):
            out.append({"index": r * cols + c, "row": r, "col": c, "x": x, "y": y, "w": cw[c], "h": rh[r]})
            x += cw[c] + gap
        y += rh[r] + gap
    return out


def letter(i: int, style: str) -> str:
    ch = chr(ord("A") + i) if i < 26 else f"A{i}"
    if style in ("a", "(a)", "a."):
        ch = ch.lower()
    return {"(A)": f"({ch})", "(a)": f"({ch})", "A.": f"{ch}.", "a.": f"{ch}."}.get(style, ch)


_ROOT = re.compile(r"^<svg\b[^>]*>", re.S)


def _inner(svg: str, k: int) -> str:
    body = _ROOT.sub("", svg, count=1)
    body = body[: body.rfind("</svg>")]
    # ids must stay unique once several figures share one document
    return re.sub(r'(id="|url\(#)([A-Za-z_][\w-]*)', lambda m: f"{m.group(1)}p{k}-{m.group(2)}", body)


def _font_of(svg: str) -> str:
    m = re.search(r'font-family="([^"]*)"', svg[:400])
    return m.group(1) if m else "Arial, Helvetica, sans-serif"


def compose(cfg: dict, placed: list[dict]) -> str:
    """placed: [{cell, svg | None, error | None}] in panel order. Empty cells are left blank (dashed box in the editor only)."""
    W, H = cfg["width_mm"] * MM, cfg["height_mm"] * MM
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{cfg["width_mm"]:g}mm" height="{cfg["height_mm"]:g}mm" '
             f'viewBox="0 0 {W:.2f} {H:.2f}" font-family="{cfg["label_font"]}">']
    if cfg["background"] != "transparent":
        parts.append(f'<rect x="0" y="0" width="{W:.2f}" height="{H:.2f}" fill="#FFFFFF"/>')
    n = 0
    for k, p in enumerate(placed):
        c = p["cell"]
        x, y = c["x"] * MM, c["y"] * MM
        parts.append(f'<g data-panel="{c["index"]}" transform="translate({x:.2f} {y:.2f})">')
        if p.get("svg"):
            parts.append(f'<g font-family="{_font_of(p["svg"])}">{_inner(p["svg"], k)}</g>')
            if cfg["label_show"]:
                lab = letter(n, cfg["label_style"])
                size = float(cfg["label_size_pt"])
                weight = "bold" if cfg["label_bold"] else "normal"
                parts.append(f'<text data-el="panel-label" x="{size * 0.15:.2f}" y="{size * 0.9:.2f}" font-size="{size:.2f}" '
                             f'font-weight="{weight}" fill="#000">{lab}</text>')
            n += 1
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)

