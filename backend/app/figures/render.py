"""Server-side SVG figure renderer.

The SVG produced here is both the on-screen preview and the source of every
export (PDF and PNG are converted from it), so exports match the preview.
Physical size is written in mm; internal coordinates are in points (1/72 in),
so font sizes and line widths in pt are exact at the final size.
"""
from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from xml.sax.saxutils import escape

import numpy as np

from ..data import DataError, Group, _label, average_technical, col_by_id, design_for, excluded_row_ids, extract_groups
from ..stats.core import describe
from . import richtext as rt

MM = 72 / 25.4  # points per mm

PALETTES = {
    "okabe_ito": ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#56B4E9", "#CC79A7", "#F0E442", "#000000"],
    "tol_bright": ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB"],
    "tol_muted": ["#332288", "#88CCEE", "#44AA99", "#117733", "#999933", "#DDCC77", "#CC6677", "#882255", "#AA4499"],
    "grayscale": ["#1A1A1A", "#737373", "#B3B3B3", "#404040", "#999999", "#D9D9D9"],
}

FONT_STACK = {
    "Arial": "Arial, 'Liberation Sans', Helvetica, sans-serif",
    "Helvetica": "Helvetica, Arial, 'Liberation Sans', sans-serif",
    "Times New Roman": "'Times New Roman', 'Liberation Serif', Times, serif",
    "DejaVu Sans": "'DejaVu Sans', Verdana, sans-serif",
}

DEFAULTS = {
    "type": "scatter_groups",
    "width_mm": 80.0,
    "height_mm": 70.0,
    "title": "",
    "x_label": "",
    "y_label": "",
    "y_unit": "",
    "x_unit": "",
    "font_family": "Arial",
    "font_size_pt": 8.0,
    "title_size_pt": 9.0,
    "line_width_pt": 0.75,
    "point_size_pt": 4.0,
    "point_shape": "circle",
    "point_opacity": 0.85,
    "palette": "okabe_ito",
    "colors": {},
    "group_order": None,
    "group_labels": {},
    "y_min": None,
    "y_max": None,
    "x_min": None,
    "x_max": None,
    "y_scale": "linear",
    "x_scale": "linear",
    "center": "mean",
    "error": "sd",
    "show_points": True,
    "jitter": 0.5,
    "legend": "auto",
    "average_technical": False,
    "regression_line": True,
    "annotations": [],
    "background": "white",
    # axes & frame (Prism-like)
    "frame": "open",            # open = left + bottom axes; closed = box on four sides
    "tick_direction": "out",    # out | in | none
    "tick_length_pt": 3.0,
    "minor_ticks": 0,           # minor ticks between two major ticks (numeric axes)
    "y_tick_step": None,
    "x_tick_step": None,
    "fill_opacity": 0.3,
    "title_dx": 0.0, "title_dy": 0.0,   # title offset (pt), set by dragging
    "legend_dx": 0.0, "legend_dy": 0.0,  # legend offset (pt), set by dragging
}

ERROR_LABEL = {
    "sd": ("SD", "ET"),
    "sem": ("SEM", "ESM"),
    "ci95": ("95% CI of the mean", "IC à 95 % de la moyenne"),
    "iqr": ("interquartile range", "écart interquartile"),
    "range": ("range (min–max)", "étendue (min–max)"),
    "none": ("", ""),
}


@dataclass
class Scale:
    lo: float
    hi: float
    p0: float
    p1: float
    log: bool = False

    def __call__(self, v: float) -> float:
        if self.log:
            v = math.log10(v) if v > 0 else math.log10(self.lo_raw)
            lo, hi = math.log10(self.lo_raw), math.log10(self.hi_raw)
        else:
            lo, hi = self.lo, self.hi
        if hi == lo:
            return (self.p0 + self.p1) / 2
        return self.p0 + (v - lo) / (hi - lo) * (self.p1 - self.p0)

    @property
    def lo_raw(self):
        return self.lo

    @property
    def hi_raw(self):
        return self.hi


def nice_step(span: float, target: int = 5) -> float:
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def nice_range(lo: float, hi: float, include_zero: bool) -> tuple[float, float, list[float]]:
    if include_zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    step = nice_step(hi - lo)
    nlo = math.floor(lo / step + 1e-9) * step
    nhi = math.ceil(hi / step - 1e-9) * step
    ticks = [round(nlo + i * step, 10) for i in range(int(round((nhi - nlo) / step)) + 1)]
    return nlo, nhi, ticks


def ticks_in(lo: float, hi: float) -> list[float]:
    step = nice_step(hi - lo)
    start = math.ceil(lo / step - 1e-9) * step
    out = []
    v = start
    while v <= hi + step * 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def log_ticks(lo: float, hi: float) -> list[float]:
    a, b = math.floor(math.log10(lo) + 1e-9), math.ceil(math.log10(hi) - 1e-9)
    return [10.0**k for k in range(a, b + 1)]


def fmt_tick(v: float) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1e5 or abs(v) < 1e-3:
        e = int(math.floor(math.log10(abs(v))))
        m = v / 10**e
        return (f"{m:g}×10" if m != 1 else "10") + f"<sup>{e}</sup>"
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s.replace("-", "−")


def text_width(s: str, size: float) -> float:
    """Width in pt, measured with real font metrics; understands the rich-text markup."""
    return rt.width(s, size)


def label_width(s: str, size: float) -> float:
    return text_width(s, size)


def van_der_corput(i: int) -> float:
    v, denom = 0.0, 1.0
    i += 1
    while i:
        denom *= 2
        i, r = divmod(i, 2)
        v += r / denom
    return v


def attr(v) -> str:
    return escape(str(v), {'"': "&quot;"})


class SVG:
    def __init__(self, w: float, h: float, cfg: dict):
        self.w, self.h, self.cfg = w, h, cfg
        self.items: list[str] = []
        self.font = FONT_STACK.get(cfg["font_family"], FONT_STACK["Arial"])

    def add(self, s: str):
        self.items.append(s)

    def open(self, el: str, **data):
        """Start a selectable element group: the editor uses data-el to know what was clicked."""
        extra = "".join(f' data-{k.replace("_", "-")}="{attr(v)}"' for k, v in data.items() if v is not None)
        self.add(f'<g data-el="{attr(el)}"{extra}>')

    def close(self):
        self.add("</g>")

    def text(self, x, y, s, size=None, anchor="middle", weight="normal", rotate=None, baseline="auto", cls="", extra=""):
        size = size or self.cfg["font_size_pt"]
        tr = f' transform="rotate({rotate} {x:.2f} {y:.2f})"' if rotate is not None else ""
        db = f' dominant-baseline="{baseline}"' if baseline != "auto" else ""
        c = f' class="{cls}"' if cls else ""
        if rt.is_rich(s):
            self._rich_text(x, y, s, size, anchor, weight, rotate, db, c, extra)
            return
        content = escape(html.unescape(s))
        self.add(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size:.2f}" text-anchor="{anchor}" font-weight="{weight}"{db}{tr}{c}{extra}>{content}</text>'
        )

    def _rich_text(self, x, y, s, size, anchor, weight, rotate, db, c, extra):
        """Styled text: every run is its own <text> at an absolute x measured with the real font metrics,
        so the browser preview and the CairoSVG export (PDF/PNG) place it identically."""
        fam = rt.family_key(self.cfg["font_family"])
        runs = rt.parse(s)
        ws = [rt.measure(t, rt.run_size(size, st), weight == "bold" or st.b, st.i, fam) for t, st in runs]
        total = sum(ws)
        cx = x - total / 2 if anchor == "middle" else (x - total if anchor == "end" else x)
        if rotate is not None:
            self.add(f'<g transform="rotate({rotate} {x:.2f} {y:.2f})">')
        for n, ((t, st), w) in enumerate(zip(runs, ws)):
            sz = rt.run_size(size, st)
            dy = -size * 0.38 if st.shift == "sup" else (size * 0.2 if st.shift == "sub" else 0.0)
            bold = weight == "bold" or st.b
            attrs = f' font-weight="bold"' if bold else ' font-weight="normal"'
            if st.i:
                attrs += ' font-style="italic"'
            if st.color:
                attrs += f' fill="{escape(st.color)}"'
            first = (c + extra) if n == 0 else ""
            self.add(f'<text x="{cx:.2f}" y="{y + dy:.2f}" font-size="{sz:.2f}" text-anchor="start" xml:space="preserve"{attrs}{db}{first}>{escape(t)}</text>')
            if st.u:
                uy = y + dy + size * 0.13
                self.add(f'<line x1="{cx:.2f}" y1="{uy:.2f}" x2="{cx + w:.2f}" y2="{uy:.2f}" stroke="{escape(st.color or "#000")}" stroke-width="{max(size * 0.06, 0.3):.2f}"/>')
            cx += w
        if rotate is not None:
            self.add("</g>")

    def line(self, x1, y1, x2, y2, color="#000", width=None, extra=""):
        width = width or self.cfg["line_width_pt"]
        self.add(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="{width:.2f}" stroke-linecap="butt"{extra}/>')

    def marker(self, x, y, color, shape=None, size=None, opacity=None, filled=True, extra=""):
        size = size or self.cfg["point_size_pt"]
        shape = shape or self.cfg["point_shape"]
        opacity = self.cfg["point_opacity"] if opacity is None else opacity
        r = size / 2
        fill = color if filled else "none"
        sw = max(self.cfg["line_width_pt"] * 0.6, 0.3)
        common = f'fill="{fill}" fill-opacity="{opacity:.2f}" stroke="{color}" stroke-width="{sw:.2f}"'
        if shape == "square":
            self.add(f'<rect x="{x - r * 0.9:.2f}" y="{y - r * 0.9:.2f}" width="{r * 1.8:.2f}" height="{r * 1.8:.2f}" {common}{extra}/>')
        elif shape == "triangle":
            h = r * 1.1
            self.add(f'<polygon points="{x:.2f},{y - h:.2f} {x - h:.2f},{y + h * 0.8:.2f} {x + h:.2f},{y + h * 0.8:.2f}" {common}{extra}/>')
        elif shape == "diamond":
            self.add(f'<polygon points="{x:.2f},{y - r * 1.2:.2f} {x + r:.2f},{y:.2f} {x:.2f},{y + r * 1.2:.2f} {x - r:.2f},{y:.2f}" {common}{extra}/>')
        elif shape == "open_circle":
            self.add(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="none" stroke="{color}" stroke-width="{sw * 1.4:.2f}"{extra}/>')
        else:
            self.add(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" {common}{extra}/>')

    def render(self) -> str:
        wmm, hmm = self.cfg["width_mm"], self.cfg["height_mm"]
        bg = "" if self.cfg["background"] == "transparent" else f'<rect x="0" y="0" width="{self.w:.2f}" height="{self.h:.2f}" fill="#FFFFFF"/>'
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{wmm:g}mm" height="{hmm:g}mm" '
            f'viewBox="0 0 {self.w:.2f} {self.h:.2f}" font-family="{self.font}">'
            f"{bg}" + "".join(self.items) + "</svg>"
        )


class FigureError(Exception):
    def __init__(self, message: dict):
        self.message = message


def _err(en: str, fr: str) -> FigureError:
    return FigureError({"en": en, "fr": fr})


def merged_config(cfg: dict) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg or {}).items() if v is not None or k in ("y_min", "y_max", "x_min", "x_max", "y_tick_step", "x_tick_step")})
    out["width_mm"] = float(min(max(out["width_mm"], 20), 500))
    out["height_mm"] = float(min(max(out["height_mm"], 20), 500))
    return out


def color_for(cfg: dict, name: str, i: int) -> str:
    c = (cfg.get("colors") or {}).get(name)
    if c:
        return c
    pal = PALETTES.get(cfg["palette"], PALETTES["okabe_ito"])
    return pal[i % len(pal)]


def _center_error(vals: list[float], center: str, error: str) -> tuple[float | None, float | None, float | None]:
    if not vals:
        return None, None, None
    d = describe(vals)
    c = d["mean"] if center == "mean" else d["median"]
    if error == "none":
        return c, None, None
    if error in ("sd", "sem", "ci95"):
        if d["n"] < 2:
            return c, None, None
        if error == "sd":
            return c, c - d["sd"], c + d["sd"]
        if error == "sem":
            return c, c - d["sem"], c + d["sem"]
        return c, d["ci_low"], d["ci_high"]
    if error == "iqr":
        return c, d["q1"], d["q3"]
    if error == "range":
        return c, d["min"], d["max"]
    return c, None, None


def caption(cfg: dict, groups: list[tuple[str, int]], lang: str) -> str:
    t = cfg["type"]
    center = cfg["center"]
    err = cfg["error"]
    e_en, e_fr = ERROR_LABEL.get(err, ("", ""))
    c_en = "mean" if center == "mean" else "median"
    c_fr = "moyenne" if center == "mean" else "médiane"
    ns = ", ".join(f"{n}: n = {k}" for n, k in groups)
    if lang == "fr":
        parts = {
            "scatter_groups": f"Points : valeurs individuelles. Trait horizontal : {c_fr}" + (f" ; barres d’erreur : {e_fr}" if err != "none" else ""),
            "bar": f"Barres : {c_fr}" + (f" ; barres d’erreur : {e_fr}" if err != "none" else "") + ("; points : valeurs individuelles" if cfg["show_points"] else ""),
            "box": "Boîtes : médiane et quartiles (Q1–Q3) ; moustaches : valeurs les plus extrêmes à moins de 1,5 × EIQ des quartiles" + ("; points : valeurs individuelles" if cfg["show_points"] else "; points : valeurs au-delà des moustaches"),
            "violin": "Violons : densité par noyau gaussien (largeur de Scott), limitée à l’étendue des données ; trait : médiane" + ("; points : valeurs individuelles" if cfg["show_points"] else ""),
            "xy": "Points : observations individuelles" + ("; droite : régression linéaire par moindres carrés" if cfg["regression_line"] else ""),
            "line": f"Points : {c_fr} à chaque valeur de X" + (f" ; barres d’erreur : {e_fr}" if err != "none" else ""),
        }[t]
        return f"{parts}. {ns}."
    parts = {
        "scatter_groups": f"Points: individual values. Horizontal line: {c_en}" + (f"; error bars: {e_en}" if err != "none" else ""),
        "bar": f"Bars: {c_en}" + (f"; error bars: {e_en}" if err != "none" else "") + ("; points: individual values" if cfg["show_points"] else ""),
        "box": "Boxes: median and quartiles (Q1–Q3); whiskers: most extreme values within 1.5 × IQR of the quartiles" + ("; points: individual values" if cfg["show_points"] else "; points: values beyond the whiskers"),
        "violin": "Violins: Gaussian kernel density (Scott bandwidth), clipped to the data range; line: median" + ("; points: individual values" if cfg["show_points"] else ""),
        "xy": "Points: individual observations" + ("; line: least-squares linear regression" if cfg["regression_line"] else ""),
        "line": f"Points: {c_en} at each X value" + (f"; error bars: {e_en}" if err != "none" else ""),
    }[t]
    return f"{parts}. {ns}."


DEFAULT_STARS = [0.05, 0.01, 0.001, 0.0001]  # GraphPad Prism default thresholds


def stars(p: float, thresholds=None) -> str:
    th = sorted([float(t) for t in (thresholds or DEFAULT_STARS)], reverse=True)
    n = sum(1 for t in th if p < t)
    return "*" * n if n else "ns"


def figure_p_format(cfg: dict) -> str:
    """One label style for the whole figure: "stars" or "exact". Older figures stored it per annotation."""
    if cfg.get("p_format") in ("stars", "exact"):
        return cfg["p_format"]
    anns = [a for a in cfg.get("annotations") or [] if a.get("type") == "pvalue"]
    return next((a.get("format") for a in anns if a.get("all")), None) or next((a.get("format") for a in anns), None) or "stars"


def p_label(p: float, p_display: str, style: str, thresholds=None) -> str:
    if style == "stars":
        return stars(p, thresholds)
    if p_display.startswith("<"):
        m, e = p_display[2:].split("e")
        return f"p < {m}×10<sup>{int(e)}</sup>".replace("-", "−")
    if "e" in p_display:
        m, e = p_display.split("e")
        return f"p = {float(m):.2g}×10<sup>{int(e)}</sup>".replace("-", "−")
    return f"p = {p_display}"


def render_figure(content: dict, cfg_in: dict, results: dict[str, dict] | None = None, lang: str = "en") -> dict:
    """Return {"svg", "caption", "warnings"}.

    `results` maps analysis_id → {"result": ..., "stale": bool} for p-value annotations.
    """
    cfg = merged_config(cfg_in)
    results = results or {}
    if cfg["type"] in ("scatter_groups", "bar", "box", "violin"):
        if not cfg["y_label"] and cfg.get("value_col") and col_by_id(content, cfg["value_col"]):
            cfg["y_label"] = col_by_id(content, cfg["value_col"])["name"]
        if not cfg["x_label"] and cfg.get("group_col") and col_by_id(content, cfg["group_col"]):
            cfg["x_label"] = col_by_id(content, cfg["group_col"])["name"]
    else:
        ys = cfg.get("y") or []
        ys = [ys] if isinstance(ys, str) else ys
        if not cfg["x_label"] and col_by_id(content, cfg.get("x")):
            cfg["x_label"] = col_by_id(content, cfg["x"])["name"]
        if not cfg["y_label"] and len(ys) == 1 and col_by_id(content, ys[0]):
            cfg["y_label"] = col_by_id(content, ys[0])["name"]
    W, H = cfg["width_mm"] * MM, cfg["height_mm"] * MM
    svg = SVG(W, H, cfg)
    fs = cfg["font_size_pt"]
    warnings: list[dict] = []
    t = cfg["type"]

    if t in ("scatter_groups", "bar", "box", "violin"):
        groups = extract_groups(content, design_for(content, cfg))
        if cfg.get("average_technical"):
            groups = {k: average_technical(g) for k, g in groups.items()}
        order = cfg.get("group_order") or list(groups.keys())
        sel: list[Group] = [groups[n] for n in order if n in groups]
        if not sel:
            raise _err("No groups to plot. Check the table design.", "Aucun groupe à tracer. Vérifiez le plan du tableau.")
        cap = caption(cfg, [(rt.plain(cfg["group_labels"].get(g.name, g.name)), len(g.values)) for g in sel], lang)
        _render_groups(svg, cfg, sel, results, warnings)
    elif t in ("xy", "line"):
        cap = _render_xy(svg, cfg, content, warnings, lang)
    else:
        raise _err(f"Unknown figure type {t}.", f"Type de figure inconnu {t}.")

    for k, a in enumerate(cfg.get("annotations") or []):
        if a.get("type") == "text" and a.get("text"):
            # Manual annotation: kept identifiable in the SVG and the caption.
            svg.open("annotation", index=k, drag="annotation")
            svg.text(a.get("x", 0.5) * W, a.get("y", 0.1) * H, a["text"], size=a.get("size_pt", fs), weight="bold" if a.get("bold") else "normal",
                     cls="manual-annotation", extra=' data-manual="true"')
            svg.close()
    p_anns = [a for a in cfg.get("annotations") or [] if a.get("type") == "pvalue" and results.get(a.get("analysis_id"))]
    if p_anns:
        tests = []
        for a in p_anns:
            r = results[a["analysis_id"]]["result"]
            t_name = (r.get("title") or {}).get(lang, "")
            fam = r.get("family")
            if fam:
                from ..stats.analyses import CORR_TEXT, PAIR_TEST
                li = 1 if lang == "fr" else 0
                corr = CORR_TEXT.get(fam["correction"], (fam["correction"],) * 2)[li]
                if fam["correction"] != "none":
                    corr = ("correction de " if lang == "fr" else "") + corr + ("" if lang == "fr" else " correction")
                test_txt = PAIR_TEST[fam["test"]][2][li] if fam["test"] in PAIR_TEST else fam["test"]
                t_name += f" ({test_txt}, {corr})"
            if t_name and t_name not in tests:
                tests.append(t_name)
        cap += (" Tests : " if lang == "fr" else " Tests: ") + "; ".join(tests) + "."
        if figure_p_format(cfg) == "stars":
            th = sorted([float(t) for t in (cfg.get("star_thresholds") or DEFAULT_STARS)], reverse=True)
            parts = [f"{'*' * (i + 1)} p < {t:g}" for i, t in enumerate(th)]
            ns = f"ns p ≥ {th[0]:g}"
            txt = ", ".join(parts) + (f", {ns}" if not cfg.get("hide_ns") else "")
            cap += f" {txt}." if lang == "en" else f" {txt.replace('.', ',')}."
    if any(a.get("type") == "text" and a.get("text") for a in cfg.get("annotations") or []):
        cap += " Manual annotation(s) added by the author." if lang == "en" else " Annotation(s) manuelle(s) ajoutée(s) par l’auteur."
    return {"svg": svg.render(), "caption": cap, "warnings": warnings}


def _frame(svg: SVG, cfg: dict, n_cats: int | None, xlabels: list[str] | None, ylo: float, yhi: float, yticks: list[float], log: bool, xscale=None, xticks=None, extra_top: float = 0.0):
    """Compute plot area and draw axes. Returns (left, right, top, bottom, yscale)."""
    W, H = svg.w, svg.h
    fs = cfg["font_size_pt"]
    lw = cfg["line_width_pt"]
    top = 4 + (cfg["title_size_pt"] * 1.6 if cfg["title"] else 0) + fs * 0.6 + extra_top
    ytick_w = max((label_width(fmt_tick(v), fs) for v in yticks), default=fs)
    ylab = cfg["y_label"] + (f" ({cfg['y_unit']})" if cfg["y_unit"] else "")
    left = 3 + (fs * 1.4 if ylab else 0) + ytick_w + fs * 0.6 + 3
    # x labels: rotate if they do not fit
    bottom_pad = fs * 1.8 + max(tick_span(cfg)[0] - 3, 0)
    rotate = False
    if xlabels is not None and n_cats:
        avail = (W - left - 6) / n_cats
        ws = [text_width(s, fs) for s in xlabels]
        widest = max(ws, default=0)
        # rotate only when two neighbouring labels would actually touch (or one overflows the figure edge)
        collide = any((ws[i] + ws[i + 1]) / 2 > avail - fs * 0.4 for i in range(len(ws) - 1)) or (ws and ws[-1] / 2 > avail / 2 + 4)
        if collide:
            rotate = True
            bottom_pad = max(tick_span(cfg)[0], 0) + 2 + min(widest, H * 0.4) * 0.71 + fs * 0.75 + 2
    xlab = cfg["x_label"] + (f" ({cfg['x_unit']})" if cfg.get("x_unit") else "")
    bottom = H - 3 - (fs * 1.5 if xlab else 0) - bottom_pad
    right = W - 6
    if bottom - top < 20 or right - left < 20:
        raise _err("The figure is too small for its text. Increase the size or reduce the font.", "La figure est trop petite pour son texte. Agrandissez-la ou réduisez la police.")

    ys = Scale(ylo, yhi, bottom, top, log)
    # never let tick labels overlap: keep every k-th tick when the plot is short for the font
    if not log and len(yticks) > 2:
        step = abs(yticks[1] - yticks[0]) or 1.0
        gap = abs(ys(yticks[0]) - ys(yticks[1]))
        if gap < fs * 1.35:
            k = 2 if gap * 2 >= fs * 1.35 else (5 if gap * 5 >= fs * 1.35 else 10)
            thinned = [v for v in yticks if abs(round(v / step) % k) == 0]
            yticks = thinned if len(thinned) >= 2 else [yticks[0], yticks[-1]]
    out, _inn = tick_span(cfg)
    # transparent plot area: clicking it selects the frame / axes settings
    svg.add(f'<rect data-el="plot" x="{left:.2f}" y="{top:.2f}" width="{right - left:.2f}" height="{bottom - top:.2f}" fill="#FFFFFF" fill-opacity="0"/>')
    # title (draggable)
    if cfg["title"]:
        svg.open("title", drag="title")
        svg.text((left + right) / 2 + float(cfg.get("title_dx") or 0), 4 + cfg["title_size_pt"] + float(cfg.get("title_dy") or 0), cfg["title"], size=cfg["title_size_pt"], weight="bold")
        svg.close()
    # y axis
    svg.open("y-axis")
    svg.line(left, top, left, bottom, width=lw)
    draw_y_ticks(svg, cfg, left, ys, yticks, ylo, yhi, log)
    for v in yticks:
        svg.text(left - out - 1.5, ys(v) + fs * 0.35, fmt_tick(v), anchor="end")
    svg.close()
    if ylab:
        svg.open("y-label")
        svg.text(3 + fs * 0.85, (top + bottom) / 2, ylab, rotate=-90)
        svg.close()
    # x axis line (ticks are drawn by the caller)
    svg.open("x-axis")
    svg.line(left, bottom, right, bottom, width=lw)
    svg.close()
    if cfg.get("frame") == "closed":
        svg.open("frame")
        svg.line(left, top, right, top, width=lw)
        svg.line(right, top, right, bottom, width=lw)
        svg.close()
    if xlab:
        svg.open("x-label")
        svg.text((left + right) / 2, H - 3 - fs * 0.3, xlab)
        svg.close()
    return left, right, top, bottom, ys, rotate


def _group_values_range(sel: list[Group], cfg: dict) -> tuple[float, float]:
    allv = [v for g in sel for v in g.values]
    lo, hi = (min(allv), max(allv)) if allv else (0.0, 1.0)
    if cfg["type"] == "bar" or cfg["type"] == "scatter_groups":
        for g in sel:
            c, el, eh = _center_error(g.values, cfg["center"], cfg["error"])
            for v in (c, el, eh):
                if v is not None:
                    lo, hi = min(lo, v), max(hi, v)
    return lo, hi


def _y_axis(cfg: dict, lo: float, hi: float, include_zero: bool, warnings: list) -> tuple[float, float, list[float], bool]:
    log = cfg["y_scale"] == "log10"
    if log:
        if lo <= 0:
            warnings.append({"en": "Log scale: values ≤ 0 cannot be shown and were left out of the plot.", "fr": "Échelle log : les valeurs ≤ 0 ne peuvent pas être affichées et ont été omises du graphique."})
        pos_lo = lo if lo > 0 else None
        ylo = cfg["y_min"] if cfg["y_min"] and cfg["y_min"] > 0 else (10 ** math.floor(math.log10(pos_lo)) if pos_lo else 0.1)
        yhi = cfg["y_max"] if cfg["y_max"] and cfg["y_max"] > 0 else 10 ** math.ceil(math.log10(max(hi, ylo * 10)))
        return ylo, yhi, log_ticks(ylo, yhi), True
    ylo, yhi, ticks = nice_range(lo, hi, include_zero)
    if cfg["y_min"] is not None:
        ylo = float(cfg["y_min"])
    if cfg["y_max"] is not None:
        yhi = float(cfg["y_max"])
    if cfg["y_min"] is not None or cfg["y_max"] is not None:
        ticks = ticks_in(ylo, yhi)
    if yhi <= ylo:
        yhi = ylo + 1
        ticks = ticks_in(ylo, yhi)
    ticks = stepped_ticks(ylo, yhi, cfg.get("y_tick_step")) or ticks
    return ylo, yhi, ticks, False


def stepped_ticks(lo: float, hi: float, step) -> list[float] | None:
    try:
        step = float(step) if step else None
    except (TypeError, ValueError):
        step = None
    if not step or step <= 0 or (hi - lo) / step > 200:
        return None
    start = math.ceil(lo / step - 1e-9) * step
    out, v = [], start
    while v <= hi + step * 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def minor_positions(ticks: list[float], n: int, log: bool, lo: float, hi: float) -> list[float]:
    if n <= 0 or len(ticks) < 1:
        return []
    out = []
    if log:
        for t in ticks:
            for m in range(2, 10):
                v = t * m
                if lo <= v <= hi:
                    out.append(v)
        return out
    if len(ticks) < 2:
        return []
    step = ticks[1] - ticks[0]
    sub = step / (n + 1)
    v = ticks[0] - step
    while v <= hi + 1e-12:
        for k in range(1, n + 1):
            w = v + k * sub
            if lo - 1e-12 <= w <= hi + 1e-12:
                out.append(w)
        v += step
    return out


def tick_span(cfg: dict) -> tuple[float, float]:
    """Return (outward, inward) extent of a major tick from the axis line."""
    L = float(cfg.get("tick_length_pt") or 3.0)
    d = cfg.get("tick_direction", "out")
    if d == "in":
        return 0.0, L
    if d == "none":
        return 0.0, 0.0
    return L, 0.0


def draw_y_ticks(svg, cfg, left, ys, ticks, lo, hi, log):
    out, inn = tick_span(cfg)
    lw = cfg["line_width_pt"]
    for v in ticks:
        if out or inn:
            svg.line(left - out, ys(v), left + inn, ys(v), width=lw)
    for v in minor_positions(ticks, int(cfg.get("minor_ticks") or 0), log, lo, hi):
        if out or inn:
            svg.line(left - out * 0.55, ys(v), left + inn * 0.55, ys(v), width=lw * 0.8)


def draw_x_ticks(svg, cfg, bottom, xs, ticks, lo, hi, log):
    out, inn = tick_span(cfg)
    lw = cfg["line_width_pt"]
    for v in ticks:
        if out or inn:
            svg.line(xs(v), bottom - inn, xs(v), bottom + out, width=lw)
    for v in minor_positions(ticks, int(cfg.get("minor_ticks") or 0), log, lo, hi):
        if out or inn:
            svg.line(xs(v), bottom - inn * 0.55, xs(v), bottom + out * 0.55, width=lw * 0.8)


def _render_groups(svg: SVG, cfg: dict, sel: list[Group], results: dict, warnings: list):
    fs = cfg["font_size_pt"]
    lw = cfg["line_width_pt"]
    lo, hi = _group_values_range(sel, cfg)
    all_ann = cfg.get("annotations") or []
    names = [g.name for g in sel]
    brackets = []  # (annotation index, comparison, label)
    p_idx = [k for k, a in enumerate(all_ann) if a.get("type") == "pvalue"]
    # One label style for the whole figure (stars or exact p). Older figures stored it per annotation.
    p_format = figure_p_format(cfg)
    seen_pairs: set[frozenset] = set()  # one bracket per pair of groups, whatever the number of annotations
    for k in sorted(p_idx, key=lambda j: (not all_ann[j].get("all"), -j)):  # "all pairs" sets first, newest first
        a = all_ann[k]
        entry = results.get(a.get("analysis_id"))
        if not entry or entry["result"].get("status") != "ok":
            warnings.append({"en": "A p-value annotation refers to an analysis that has no valid result. Run the analysis first.",
                             "fr": "Une annotation de p renvoie à une analyse sans résultat valide. Lancez d’abord l’analyse."})
            continue
        comps = entry["result"].get("comparisons") or []
        if not a.get("all"):
            pick = next((c for c in comps if {c["group1"], c["group2"]} == {a.get("group1"), a.get("group2")}), None)
            comps = [pick or (comps[0] if comps else None)] if comps else []
        shown = [c for c in comps if c and c["group1"] in names and c["group2"] in names]
        if len(shown) < len([c for c in comps if c]):
            warnings.append({"en": "Some compared groups are not shown in this figure.", "fr": "Certains groupes comparés ne sont pas affichés dans cette figure."})
        if entry.get("stale"):
            warnings.append({"en": "A p-value shown comes from an analysis computed on an older version of the data or settings. Re-run it.",
                             "fr": "Une valeur de p affichée provient d’une analyse calculée sur une ancienne version des données ou des réglages. Relancez-la."})
        for c in shown:
            pair = frozenset((c["group1"], c["group2"]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            label = p_label(c["p"], c["p_display"], p_format, cfg.get("star_thresholds"))
            if label == "ns" and cfg.get("hide_ns"):
                continue
            brackets.append((k, c, label))
    labels = [cfg["group_labels"].get(g.name, g.name) for g in sel]
    include_zero = cfg["type"] == "bar"
    ylo, yhi, yticks, log = _y_axis(cfg, lo, hi, include_zero=include_zero, warnings=[])
    # Brackets are laid out in points above the highest data value. Their whole stack gets a fixed budget
    # (at most ~40 % of the plot height, so the data are never crushed): labels and spacing shrink to fit it.
    # The room is then reserved by extending the automatic Y range, or, for a fixed Y max / log scale, by
    # drawing the stack above the plot in an enlarged top margin. Never across the frame or the title.
    hi_target, extra_top, above_plot = hi, 0.0, False
    layout: list = []
    label_fs, bracket_gap, level_h, needed = fs, 6.0, fs * 2.0, 0.0
    exact = p_format == "exact"
    if brackets:
        l0, r0, ptop0, pbottom0, _ys0, _rot = _frame(SVG(svg.w, svg.h, cfg), cfg, len(sel), labels, ylo, yhi, yticks, log)
        hp0 = pbottom0 - ptop0
        band0 = (r0 - l0) / len(sel)
        budget = hp0 * 0.4

        def stack(lfs: float) -> list:
            placed, out = [], []  # placed: (x_from, x_to, level), label width included so labels never collide
            for k, c, label in sorted(brackets, key=lambda b: abs(names.index(b[1]["group1"]) - names.index(b[1]["group2"]))):
                i1, i2 = sorted([names.index(c["group1"]), names.index(c["group2"])])
                x1, x2 = band0 * (i1 + 0.5) + 2, band0 * (i2 + 0.5) - 2
                w = label_width(label, lfs) + lfs * 1.2
                xa, xb = min(x1, (x1 + x2 - w) / 2), max(x2, (x1 + x2 + w) / 2)
                lvl = 0
                while any(l == lvl and not (xb < a_ - 1 or xa > b_ + 1) for a_, b_, l in placed):
                    lvl += 1
                placed.append((xa, xb, lvl))
                out.append((k, c, label, i1, i2, lvl))
            return out

        def need(n: int, lfs: float, gap: float, lh: float) -> float:
            return gap + (n - 1) * lh + 1.2 + lfs * (1.25 if exact else 0.9) + 2.0

        lfs = fs
        while True:
            layout = stack(lfs)
            n_levels = max(l for *_, l in layout) + 1
            label_fs, bracket_gap = lfs, 6.0
            level_h = lfs * (1.25 if exact else 0.9) + 1.2 + max(fs * 0.9, 6.0)
            needed = need(n_levels, lfs, bracket_gap, level_h)
            if needed > budget:  # tighten the spacing first
                bracket_gap = 3.0
                level_h = lfs * (1.25 if exact else 0.9) + 1.2 + 3.5
                needed = need(n_levels, lfs, bracket_gap, level_h)
            if needed <= budget or lfs <= max(5.0, fs * 0.55):
                break
            lfs = max(5.0, fs * 0.55, lfs * 0.9)  # then use smaller bracket labels
        if needed > budget:  # last resort before overflowing: allow a taller stack (data keep ≥ 40 %)
            budget = hp0 * 0.6
            if needed > budget:
                level_h = max((budget - bracket_gap - 3.2 - label_fs * (1.25 if exact else 0.9)) / max(n_levels - 1, 1), label_fs * 0.9)
                needed = need(n_levels, label_fs, bracket_gap, level_h)
        if needed > hp0 * 0.5:
            warnings.append({"en": "The figure is too small to show every significance bracket clearly: enlarge it, reduce the font or hide “ns”.",
                             "fr": "La figure est trop petite pour afficher clairement tous les crochets de significativité : agrandissez-la, réduisez la police ou masquez les « ns »."})
        auto_range = cfg["y_max"] is None and not log
        for _ in range(12):
            _l, _r, ptop, pbottom, pys, _rot = _frame(SVG(svg.w, svg.h, cfg), cfg, len(sel), labels, ylo, yhi, yticks, log)
            inside_room = min(pys(hi), pbottom) - ptop if hi <= yhi else -1.0
            if inside_room >= needed + 2.0:
                break
            hp = pbottom - ptop
            if auto_range and needed + 2.0 < hp * 0.65:
                # solve (yhi - hi) / (yhi - ylo) * hp >= needed + 2 for yhi, then let nice_range round it up
                want = needed + 2.0
                hi_target = max(hi_target, (hi * hp - want * ylo) / (hp - want)) + (hi - lo or 1.0) * 1e-3
                ylo, yhi, yticks, log = _y_axis(cfg, lo, hi_target, include_zero=include_zero, warnings=[])
                continue
            above_plot = True  # fixed Y range (or log scale): the stack goes above the plot
            extra_top = max(0.0, min(needed + 2.0, hp0 * 0.6, hp0 - 22.0))
            break
    ylo, yhi, yticks, log = _y_axis(cfg, lo, hi_target, include_zero=include_zero, warnings=warnings)
    left, right, top, bottom, ys, rotate = _frame(svg, cfg, len(sel), labels, ylo, yhi, yticks, log, extra_top=extra_top)

    band = (right - left) / len(sel)
    svg.add(f'<clipPath id="plotclip"><rect x="{left:.2f}" y="{top - 1:.2f}" width="{right - left:.2f}" height="{bottom - top + 1:.2f}"/></clipPath>')
    svg.add('<g clip-path="url(#plotclip)">')
    centers = {}
    t = cfg["type"]
    for i, g in enumerate(sel):
        cx = left + band * (i + 0.5)
        centers[g.name] = cx
        color = color_for(cfg, g.name, i)
        keep = [k for k, v in enumerate(g.values) if not (log and v <= 0)]
        vals = [g.values[k] for k in keep]
        rids = [g.row_ids[k] if k < len(g.row_ids) else "" for k in keep]
        half = band * 0.32
        fo = float(cfg.get("fill_opacity", 0.3))
        svg.open("group", group=g.name, index=i)
        if t == "bar":
            c, el, eh = _center_error(vals, cfg["center"], cfg["error"])
            if c is not None:
                base = ys(max(ylo, 0) if not log else ylo)
                yc = ys(c)
                svg.add(f'<rect x="{cx - half:.2f}" y="{min(yc, base):.2f}" width="{2 * half:.2f}" height="{abs(base - yc):.2f}" fill="{color}" fill-opacity="{fo:.2f}" stroke="{color}" stroke-width="{lw:.2f}"/>')
                _errbar(svg, cx, ys, el, eh, half * 0.45, lw, "#000")
        elif t == "box" and len(vals) >= 1:
            a = np.asarray(vals)
            q1, med, q3 = np.quantile(a, [0.25, 0.5, 0.75])
            iqr = q3 - q1
            lo_w = a[a >= q1 - 1.5 * iqr].min()
            hi_w = a[a <= q3 + 1.5 * iqr].max()
            svg.add(f'<rect x="{cx - half:.2f}" y="{ys(q3):.2f}" width="{2 * half:.2f}" height="{abs(ys(q1) - ys(q3)):.2f}" fill="{color}" fill-opacity="{fo:.2f}" stroke="{color}" stroke-width="{lw:.2f}"/>')
            svg.line(cx - half, ys(med), cx + half, ys(med), color=color, width=lw * 1.8)
            svg.line(cx, ys(q3), cx, ys(hi_w), color=color)
            svg.line(cx, ys(q1), cx, ys(lo_w), color=color)
            svg.line(cx - half * 0.4, ys(hi_w), cx + half * 0.4, ys(hi_w), color=color)
            svg.line(cx - half * 0.4, ys(lo_w), cx + half * 0.4, ys(lo_w), color=color)
            if not cfg["show_points"]:
                for v in a[(a < lo_w) | (a > hi_w)]:
                    svg.marker(cx, ys(v), color, shape="open_circle")
        elif t == "violin" and len(vals) >= 2 and np.ptp(vals) > 0:
            from scipy.stats import gaussian_kde

            a = np.asarray(vals)
            kde = gaussian_kde(np.log10(a) if log else a)
            grid = np.linspace(a.min(), a.max(), 80)
            dens = kde(np.log10(grid) if log else grid)
            dens = dens / dens.max() * half * 1.1
            pts_r = [f"{cx + d:.2f},{ys(v):.2f}" for v, d in zip(grid, dens)]
            pts_l = [f"{cx - d:.2f},{ys(v):.2f}" for v, d in zip(grid[::-1], dens[::-1])]
            svg.add(f'<polygon points="{" ".join(pts_r + pts_l)}" fill="{color}" fill-opacity="{fo:.2f}" stroke="{color}" stroke-width="{lw:.2f}"/>')
            med = float(np.median(a))
            md = float(kde(np.log10(med) if log else med)[0] / kde(np.log10(grid) if log else grid).max() * half * 1.1)
            svg.line(cx - md, ys(med), cx + md, ys(med), color=color, width=lw * 1.8)
        # individual points
        if cfg["show_points"] or t == "scatter_groups":
            _points(svg, cfg, vals, cx, band, ys, color, rids)
        if t == "scatter_groups":
            c, el, eh = _center_error(vals, cfg["center"], cfg["error"])
            if c is not None:
                svg.line(cx - half * 0.75, ys(c), cx + half * 0.75, ys(c), color="#000", width=lw * 1.6)
                _errbar(svg, cx, ys, el, eh, half * 0.35, lw, "#000")
        svg.close()
    svg.add("</g>")
    # x tick marks and group labels
    out, inn = tick_span(cfg)
    svg.open("x-axis")
    for i, g in enumerate(sel):
        cx = left + band * (i + 0.5)
        if out or inn:
            svg.line(cx, bottom - inn, cx, bottom + out, width=lw)
    svg.close()
    for i, g in enumerate(sel):
        cx = left + band * (i + 0.5)
        lab = labels[i]
        svg.open("group-label", group=g.name)
        if rotate:
            svg.text(cx + fs * 0.3, bottom + out + 2, lab, anchor="end", rotate=-45, baseline="hanging")
        else:
            svg.text(cx, bottom + out + 1.5 + fs, lab)
        svg.close()

    # p-value brackets / stars: only from saved analysis results
    for k, c, label, i1, i2, lvl in layout:
        x1, x2 = left + band * (i1 + 0.5), left + band * (i2 + 0.5)
        ybase = (top if above_plot else min(ys(hi), bottom)) - bracket_gap - lvl * level_h
        tick = 3
        svg.open("pvalue", index=k)
        svg.add(
            f'<path d="M{x1 + 2:.2f},{ybase + tick:.2f} L{x1 + 2:.2f},{ybase:.2f} L{x2 - 2:.2f},{ybase:.2f} L{x2 - 2:.2f},{ybase + tick:.2f}" '
            f'fill="none" stroke="#000" stroke-width="{lw:.2f}" class="pvalue-bracket" data-analysis-id="{attr(all_ann[k].get("analysis_id", ""))}"/>'
        )
        svg.text((x1 + x2) / 2, ybase - (1.2 if label.startswith("*") else 2.0), label, size=label_fs, cls="pvalue-label")
        svg.close()


def _errbar(svg: SVG, cx: float, ys, el, eh, cap: float, lw: float, color: str):
    if el is None or eh is None:
        return
    svg.line(cx, ys(el), cx, ys(eh), color=color, width=lw)
    svg.line(cx - cap, ys(el), cx + cap, ys(el), color=color, width=lw)
    svg.line(cx - cap, ys(eh), cx + cap, ys(eh), color=color, width=lw)


def _points(svg: SVG, cfg: dict, vals: list[float], cx: float, band: float, ys, color: str, row_ids: list[str] | None = None):
    width = band * 0.32 * cfg["jitter"]
    order = np.argsort(vals)
    for k, idx in enumerate(order):
        off = (van_der_corput(k) - 0.5) * 2 * width if len(vals) > 1 else 0.0
        rid = row_ids[idx] if row_ids and idx < len(row_ids) else ""
        svg.marker(cx + off, ys(vals[idx]), color, extra=f' data-row="{attr(rid)}" data-v="{vals[idx]:.6g}"')


def _render_xy(svg: SVG, cfg: dict, content: dict, warnings: list, lang: str) -> str:
    xc = cfg.get("x")
    ycols = cfg.get("y") or []
    if isinstance(ycols, str):
        ycols = [ycols]
    if not col_by_id(content, xc) or not ycols:
        raise _err("Choose an X column and at least one Y column.", "Choisissez une colonne X et au moins une colonne Y.")
    excl = excluded_row_ids(content)
    series = []
    rows_of: dict[str, list[str]] = {}
    color_by = cfg.get("color_by") if col_by_id(content, cfg.get("color_by")) else None
    for yc in ycols:
        col = col_by_id(content, yc)
        if col is None:
            continue
        by_level: dict[str, list] = {}
        for row in content["rows"]:
            if row["id"] in excl:
                continue
            x, y = row["cells"].get(xc), row["cells"].get(yc)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x is not None and y is not None:
                if (cfg["x_scale"] == "log10" and x <= 0) or (cfg["y_scale"] == "log10" and y <= 0):
                    continue
                if color_by:
                    lvl = _label(row["cells"].get(color_by))
                    if lvl is None:
                        continue
                    key = lvl if len(ycols) == 1 else f"{col['name']} – {lvl}"
                else:
                    key = col["name"]
                by_level.setdefault(key, []).append((float(x), float(y)))
                rows_of.setdefault(key, []).append(row["id"])
        series.extend(by_level.items())
    order = cfg.get("group_order")
    if order:
        series.sort(key=lambda s: order.index(s[0]) if s[0] in order else len(order))
    allx = [p[0] for _, s in series for p in s]
    ally = [p[1] for _, s in series for p in s]
    if not allx:
        raise _err("No complete (X, Y) pairs to plot.", "Aucune paire (X, Y) complète à tracer.")
    t = cfg["type"]
    summaries = []
    if t == "line":
        for name, pts in series:
            byx: dict[float, list[float]] = {}
            for x, y in pts:
                byx.setdefault(x, []).append(y)
            summ = []
            for x in sorted(byx):
                c, el, eh = _center_error(byx[x], cfg["center"], cfg["error"])
                summ.append((x, c, el, eh, len(byx[x])))
            summaries.append((name, summ))
            for _, c, el, eh, _n in summ:
                ally += [v for v in (c, el, eh) if v is not None]
    ylo, yhi, yticks, ylog = _y_axis(cfg, min(ally), max(ally), include_zero=False, warnings=warnings)
    xlog = cfg["x_scale"] == "log10"
    if xlog:
        xlo = cfg["x_min"] if cfg["x_min"] and cfg["x_min"] > 0 else 10 ** math.floor(math.log10(min(allx)))
        xhi = cfg["x_max"] if cfg["x_max"] and cfg["x_max"] > 0 else 10 ** math.ceil(math.log10(max(allx)))
        xticks = log_ticks(xlo, xhi)
    else:
        xlo, xhi, xticks = nice_range(min(allx), max(allx), False)
        if cfg["x_min"] is not None:
            xlo = float(cfg["x_min"])
        if cfg["x_max"] is not None:
            xhi = float(cfg["x_max"])
        if cfg["x_min"] is not None or cfg["x_max"] is not None:
            xticks = ticks_in(xlo, xhi)
    left, right, top, bottom, ys, _ = _frame(svg, cfg, None, None, ylo, yhi, yticks, ylog)
    xs = Scale(xlo, xhi, left + 4, right - 4, xlog)
    fs, lw = cfg["font_size_pt"], cfg["line_width_pt"]
    if not xlog:
        xticks = stepped_ticks(xlo, xhi, cfg.get("x_tick_step")) or xticks
    out, _inn = tick_span(cfg)
    svg.open("x-axis")
    draw_x_ticks(svg, cfg, bottom, xs, xticks, xlo, xhi, xlog)
    for v in xticks:
        svg.text(xs(v), bottom + out + 1.5 + fs, fmt_tick(v))
    svg.close()
    svg.add(f'<clipPath id="plotclip"><rect x="{left:.2f}" y="{top - 1:.2f}" width="{right - left:.2f}" height="{bottom - top + 1:.2f}"/></clipPath>')
    svg.add('<g clip-path="url(#plotclip)">')
    counts = []
    for i, (name, pts) in enumerate(series):
        color = color_for(cfg, name, i)
        counts.append((name, len(pts)))
        svg.open("group", group=name, index=i)
        rids = rows_of.get(name, [])
        if t == "xy":
            for k, (x, y) in enumerate(pts):
                svg.marker(xs(x), ys(y), color, extra=f' data-row="{attr(rids[k] if k < len(rids) else "")}" data-v="{y:.6g}" data-x="{x:.6g}"')
            if cfg["regression_line"] and len(pts) >= 2 and np.ptp([p[0] for p in pts]) > 0:
                X = np.array([p[0] for p in pts])
                Y = np.array([p[1] for p in pts])
                if xlog or ylog:
                    warnings.append({"en": "The regression line is fitted on linear values and drawn on a log axis.", "fr": "La droite de régression est ajustée sur les valeurs linéaires et tracée sur un axe log."})
                slope, inter = np.polyfit(X, Y, 1)
                gx = np.linspace(X.min(), X.max(), 50)
                path = " ".join(f"{'M' if k == 0 else 'L'}{xs(a):.2f},{ys(slope * a + inter):.2f}" for k, a in enumerate(gx) if not (ylog and slope * a + inter <= 0))
                svg.add(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="{lw * 1.4:.2f}"/>')
        else:
            summ = summaries[i][1]
            path = " ".join(f"{'M' if k == 0 else 'L'}{xs(x):.2f},{ys(c):.2f}" for k, (x, c, *_r) in enumerate(summ))
            svg.add(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="{lw * 1.4:.2f}"/>')
            for x, c, el, eh, _n in summ:
                _errbar(svg, xs(x), ys, el, eh, 2.0, lw, color)
                svg.marker(xs(x), ys(c), color, opacity=1.0)
            if cfg["show_points"]:
                for k, (x, y) in enumerate(pts):
                    svg.marker(xs(x), ys(y), color, size=cfg["point_size_pt"] * 0.6, opacity=0.4,
                               extra=f' data-row="{attr(rids[k] if k < len(rids) else "")}" data-v="{y:.6g}" data-x="{x:.6g}"')
        svg.close()
    svg.add("</g>")
    if (cfg["legend"] == "auto" and len(series) > 1) or cfg["legend"] is True:
        lx = right - max(text_width(n, fs) for n, _ in series) - 14
        ly = top + 2
        lx += float(cfg.get("legend_dx") or 0)
        ly += float(cfg.get("legend_dy") or 0)
        svg.open("legend", drag="legend")
        for i, (name, _) in enumerate(series):
            color = color_for(cfg, name, i)
            svg.marker(lx, ly + fs * 0.5, color)
            svg.text(lx + 6, ly + fs * 0.85, name, anchor="start")
            ly += fs * 1.4
        svg.close()
    return caption(cfg, counts, lang)


def to_pdf(svg: str) -> bytes:
    import cairosvg

    return cairosvg.svg2pdf(bytestring=svg.encode())


def to_png(svg: str, dpi: int) -> bytes:
    import cairosvg

    return cairosvg.svg2png(bytestring=svg.encode(), dpi=dpi)
