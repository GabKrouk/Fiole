"""Server-side SVG figure renderer.

The SVG produced here is both the on-screen preview and the source of every
export (PDF and PNG are converted from it), so exports match the preview.
Physical size is written in mm; internal coordinates are in points (1/72 in),
so font sizes and line widths in pt are exact at the final size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from xml.sax.saxutils import escape

import numpy as np

from ..data import DataError, Group, average_technical, col_by_id, excluded_row_ids, extract_groups
from ..stats.core import describe

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
    return 0.55 * size * len(s)


def van_der_corput(i: int) -> float:
    v, denom = 0.0, 1.0
    i += 1
    while i:
        denom *= 2
        i, r = divmod(i, 2)
        v += r / denom
    return v


class SVG:
    def __init__(self, w: float, h: float, cfg: dict):
        self.w, self.h, self.cfg = w, h, cfg
        self.items: list[str] = []
        self.font = FONT_STACK.get(cfg["font_family"], FONT_STACK["Arial"])

    def add(self, s: str):
        self.items.append(s)

    def text(self, x, y, s, size=None, anchor="middle", weight="normal", rotate=None, baseline="auto", cls="", extra=""):
        size = size or self.cfg["font_size_pt"]
        tr = f' transform="rotate({rotate} {x:.2f} {y:.2f})"' if rotate is not None else ""
        content = escape(s)
        # Superscripts for tick labels (10<sup>3</sup>)
        if "&lt;sup&gt;" in content:
            content = content.replace("&lt;sup&gt;", f'<tspan baseline-shift="super" font-size="{size * 0.7:.2f}">').replace("&lt;/sup&gt;", "</tspan>")
        db = f' dominant-baseline="{baseline}"' if baseline != "auto" else ""
        c = f' class="{cls}"' if cls else ""
        self.add(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size:.2f}" text-anchor="{anchor}" font-weight="{weight}"{db}{tr}{c}{extra}>{content}</text>'
        )

    def line(self, x1, y1, x2, y2, color="#000", width=None, extra=""):
        width = width or self.cfg["line_width_pt"]
        self.add(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="{width:.2f}" stroke-linecap="butt"{extra}/>')

    def marker(self, x, y, color, shape=None, size=None, opacity=None, filled=True):
        size = size or self.cfg["point_size_pt"]
        shape = shape or self.cfg["point_shape"]
        opacity = self.cfg["point_opacity"] if opacity is None else opacity
        r = size / 2
        fill = color if filled else "none"
        sw = max(self.cfg["line_width_pt"] * 0.6, 0.3)
        common = f'fill="{fill}" fill-opacity="{opacity:.2f}" stroke="{color}" stroke-width="{sw:.2f}"'
        if shape == "square":
            self.add(f'<rect x="{x - r * 0.9:.2f}" y="{y - r * 0.9:.2f}" width="{r * 1.8:.2f}" height="{r * 1.8:.2f}" {common}/>')
        elif shape == "triangle":
            h = r * 1.1
            self.add(f'<polygon points="{x:.2f},{y - h:.2f} {x - h:.2f},{y + h * 0.8:.2f} {x + h:.2f},{y + h * 0.8:.2f}" {common}/>')
        elif shape == "diamond":
            self.add(f'<polygon points="{x:.2f},{y - r * 1.2:.2f} {x + r:.2f},{y:.2f} {x:.2f},{y + r * 1.2:.2f} {x - r:.2f},{y:.2f}" {common}/>')
        elif shape == "open_circle":
            self.add(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="none" stroke="{color}" stroke-width="{sw * 1.4:.2f}"/>')
        else:
            self.add(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" {common}/>')

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
    out.update({k: v for k, v in (cfg or {}).items() if v is not None or k in ("y_min", "y_max", "x_min", "x_max")})
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


def stars(p: float) -> str:
    if p < 0.0001:
        return "****"
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


def p_label(p: float, p_display: str, style: str) -> str:
    if style == "stars":
        return stars(p)
    if p_display.startswith("<"):
        return f"p {p_display}"
    return f"p = {p_display}"


def render_figure(content: dict, cfg_in: dict, results: dict[str, dict] | None = None, lang: str = "en") -> dict:
    """Return {"svg", "caption", "warnings"}.

    `results` maps analysis_id → {"result": ..., "stale": bool} for p-value annotations.
    """
    cfg = merged_config(cfg_in)
    results = results or {}
    W, H = cfg["width_mm"] * MM, cfg["height_mm"] * MM
    svg = SVG(W, H, cfg)
    fs = cfg["font_size_pt"]
    warnings: list[dict] = []
    t = cfg["type"]

    if t in ("scatter_groups", "bar", "box", "violin"):
        groups = extract_groups(content)
        if cfg.get("average_technical"):
            groups = {k: average_technical(g) for k, g in groups.items()}
        order = cfg.get("group_order") or list(groups.keys())
        sel: list[Group] = [groups[n] for n in order if n in groups]
        if not sel:
            raise _err("No groups to plot. Check the table design.", "Aucun groupe à tracer. Vérifiez le plan du tableau.")
        cap = caption(cfg, [(cfg["group_labels"].get(g.name, g.name), len(g.values)) for g in sel], lang)
        _render_groups(svg, cfg, sel, results, warnings)
    elif t in ("xy", "line"):
        cap = _render_xy(svg, cfg, content, warnings, lang)
    else:
        raise _err(f"Unknown figure type {t}.", f"Type de figure inconnu {t}.")

    for a in cfg.get("annotations") or []:
        if a.get("type") == "text" and a.get("text"):
            # Manual annotation: kept identifiable in the SVG and the caption.
            svg.text(a.get("x", 0.5) * W, a.get("y", 0.1) * H, a["text"], size=a.get("size_pt", fs), cls="manual-annotation", extra=' data-manual="true"')
    if any(a.get("type") == "text" and a.get("text") for a in cfg.get("annotations") or []):
        cap += " Manual annotation(s) added by the author." if lang == "en" else " Annotation(s) manuelle(s) ajoutée(s) par l’auteur."
    return {"svg": svg.render(), "caption": cap, "warnings": warnings}


def _frame(svg: SVG, cfg: dict, n_cats: int | None, xlabels: list[str] | None, ylo: float, yhi: float, yticks: list[float], log: bool, xscale=None, xticks=None):
    """Compute plot area and draw axes. Returns (left, right, top, bottom, yscale)."""
    W, H = svg.w, svg.h
    fs = cfg["font_size_pt"]
    lw = cfg["line_width_pt"]
    top = 4 + (cfg["title_size_pt"] * 1.6 if cfg["title"] else 0) + fs * 0.6
    ytick_w = max((text_width(fmt_tick(v).split("<")[0], fs) for v in yticks), default=fs)
    ylab = cfg["y_label"] + (f" ({cfg['y_unit']})" if cfg["y_unit"] else "")
    left = 3 + (fs * 1.4 if ylab else 0) + ytick_w + fs * 0.6 + 3
    # x labels: rotate if they do not fit
    bottom_pad = fs * 1.8
    rotate = False
    if xlabels is not None and n_cats:
        avail = (W - left - 6) / n_cats
        widest = max((text_width(s, fs) for s in xlabels), default=0)
        if widest > avail * 0.95:
            rotate = True
            bottom_pad = fs * 0.8 + min(widest, H * 0.35) * 0.72 + fs
    xlab = cfg["x_label"] + (f" ({cfg['x_unit']})" if cfg.get("x_unit") else "")
    bottom = H - 3 - (fs * 1.5 if xlab else 0) - bottom_pad
    right = W - 6
    if bottom - top < 20 or right - left < 20:
        raise _err("The figure is too small for its text. Increase the size or reduce the font.", "La figure est trop petite pour son texte. Agrandissez-la ou réduisez la police.")

    ys = Scale(ylo, yhi, bottom, top, log)
    # title
    if cfg["title"]:
        svg.text((left + right) / 2, 4 + cfg["title_size_pt"], cfg["title"], size=cfg["title_size_pt"], weight="bold")
    # y axis
    svg.line(left, top, left, bottom, width=lw)
    for v in yticks:
        y = ys(v)
        svg.line(left - 3, y, left, y, width=lw)
        svg.text(left - 4.5, y + fs * 0.35, fmt_tick(v), anchor="end")
    if ylab:
        svg.text(3 + fs * 0.85, (top + bottom) / 2, ylab, rotate=-90)
    # x axis
    svg.line(left, bottom, right, bottom, width=lw)
    if xlab:
        svg.text((left + right) / 2, H - 3 - fs * 0.3, xlab)
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
    return ylo, yhi, ticks, False


def _render_groups(svg: SVG, cfg: dict, sel: list[Group], results: dict, warnings: list):
    fs = cfg["font_size_pt"]
    lw = cfg["line_width_pt"]
    lo, hi = _group_values_range(sel, cfg)
    pvals = [a for a in cfg.get("annotations") or [] if a.get("type") == "pvalue"]
    span = (hi - lo) or 1.0
    # Reserve headroom for brackets
    hi_ext = hi + span * 0.12 * len(pvals) if pvals and cfg["y_scale"] != "log10" else hi
    ylo, yhi, yticks, log = _y_axis(cfg, lo, hi_ext, include_zero=cfg["type"] == "bar", warnings=warnings)
    labels = [cfg["group_labels"].get(g.name, g.name) for g in sel]
    left, right, top, bottom, ys, rotate = _frame(svg, cfg, len(sel), labels, ylo, yhi, yticks, log)

    band = (right - left) / len(sel)
    svg.add(f'<clipPath id="plotclip"><rect x="{left:.2f}" y="{top - 1:.2f}" width="{right - left:.2f}" height="{bottom - top + 1:.2f}"/></clipPath>')
    svg.add('<g clip-path="url(#plotclip)">')
    centers = {}
    t = cfg["type"]
    for i, g in enumerate(sel):
        cx = left + band * (i + 0.5)
        centers[g.name] = cx
        color = color_for(cfg, g.name, i)
        vals = [v for v in g.values if not (log and v <= 0)]
        half = band * 0.32
        if t == "bar":
            c, el, eh = _center_error(vals, cfg["center"], cfg["error"])
            if c is not None:
                base = ys(max(ylo, 0) if not log else ylo)
                yc = ys(c)
                svg.add(f'<rect x="{cx - half:.2f}" y="{min(yc, base):.2f}" width="{2 * half:.2f}" height="{abs(base - yc):.2f}" fill="{color}" fill-opacity="0.35" stroke="{color}" stroke-width="{lw:.2f}"/>')
                _errbar(svg, cx, ys, el, eh, half * 0.45, lw, "#000")
        elif t == "box" and len(vals) >= 1:
            a = np.asarray(vals)
            q1, med, q3 = np.quantile(a, [0.25, 0.5, 0.75])
            iqr = q3 - q1
            lo_w = a[a >= q1 - 1.5 * iqr].min()
            hi_w = a[a <= q3 + 1.5 * iqr].max()
            svg.add(f'<rect x="{cx - half:.2f}" y="{ys(q3):.2f}" width="{2 * half:.2f}" height="{abs(ys(q1) - ys(q3)):.2f}" fill="{color}" fill-opacity="0.25" stroke="{color}" stroke-width="{lw:.2f}"/>')
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
            svg.add(f'<polygon points="{" ".join(pts_r + pts_l)}" fill="{color}" fill-opacity="0.25" stroke="{color}" stroke-width="{lw:.2f}"/>')
            med = float(np.median(a))
            md = float(kde(np.log10(med) if log else med)[0] / kde(np.log10(grid) if log else grid).max() * half * 1.1)
            svg.line(cx - md, ys(med), cx + md, ys(med), color=color, width=lw * 1.8)
        # individual points
        if cfg["show_points"] or t == "scatter_groups":
            _points(svg, cfg, vals, cx, band, ys, color)
        if t == "scatter_groups":
            c, el, eh = _center_error(vals, cfg["center"], cfg["error"])
            if c is not None:
                svg.line(cx - half * 0.75, ys(c), cx + half * 0.75, ys(c), color="#000", width=lw * 1.6)
                _errbar(svg, cx, ys, el, eh, half * 0.35, lw, "#000")
    svg.add("</g>")
    # x tick labels
    for i, g in enumerate(sel):
        cx = left + band * (i + 0.5)
        svg.line(cx, bottom, cx, bottom + 3, width=lw)
        lab = labels[i]
        if rotate:
            svg.text(cx + fs * 0.3, bottom + 5, lab, anchor="end", rotate=-45, baseline="hanging")
        else:
            svg.text(cx, bottom + 4.5 + fs, lab)

    # p-value brackets: only from saved analysis results
    level = 0
    for a in pvals:
        entry = results.get(a.get("analysis_id"))
        if not entry or entry["result"].get("status") != "ok":
            warnings.append({"en": "A p-value annotation refers to an analysis that has no valid result. Run the analysis first.",
                             "fr": "Une annotation de p renvoie à une analyse sans résultat valide. Lancez d’abord l’analyse."})
            continue
        comp = next((c for c in entry["result"].get("comparisons", []) if {c["group1"], c["group2"]} == {a.get("group1"), a.get("group2")}), None)
        if comp is None:
            comp = (entry["result"].get("comparisons") or [None])[0]
        if comp is None or comp["group1"] not in centers or comp["group2"] not in centers:
            warnings.append({"en": "The compared groups are not shown in this figure.", "fr": "Les groupes comparés ne sont pas affichés dans cette figure."})
            continue
        if entry.get("stale"):
            warnings.append({"en": "A p-value shown comes from an analysis computed on an older version of the data. Re-run it.",
                             "fr": "Une valeur de p affichée provient d’une analyse calculée sur une ancienne version des données. Relancez-la."})
        x1, x2 = sorted([centers[comp["group1"]], centers[comp["group2"]]])
        ybase = ys(hi) - 6 - level * (fs * 2.2)
        tick = 3
        svg.add(
            f'<path d="M{x1 + 2:.2f},{ybase + tick:.2f} L{x1 + 2:.2f},{ybase:.2f} L{x2 - 2:.2f},{ybase:.2f} L{x2 - 2:.2f},{ybase + tick:.2f}" '
            f'fill="none" stroke="#000" stroke-width="{lw:.2f}" class="pvalue-bracket" data-analysis-id="{escape(a.get("analysis_id", ""))}"/>'
        )
        svg.text((x1 + x2) / 2, ybase - 2.5, p_label(comp["p"], comp["p_display"], a.get("format", "exact")), cls="pvalue-label")
        level += 1


def _errbar(svg: SVG, cx: float, ys, el, eh, cap: float, lw: float, color: str):
    if el is None or eh is None:
        return
    svg.line(cx, ys(el), cx, ys(eh), color=color, width=lw)
    svg.line(cx - cap, ys(el), cx + cap, ys(el), color=color, width=lw)
    svg.line(cx - cap, ys(eh), cx + cap, ys(eh), color=color, width=lw)


def _points(svg: SVG, cfg: dict, vals: list[float], cx: float, band: float, ys, color: str):
    width = band * 0.32 * cfg["jitter"]
    order = np.argsort(vals)
    for k, idx in enumerate(order):
        off = (van_der_corput(k) - 0.5) * 2 * width if len(vals) > 1 else 0.0
        svg.marker(cx + off, ys(vals[idx]), color)


def _render_xy(svg: SVG, cfg: dict, content: dict, warnings: list, lang: str) -> str:
    xc = cfg.get("x")
    ycols = cfg.get("y") or []
    if isinstance(ycols, str):
        ycols = [ycols]
    if not col_by_id(content, xc) or not ycols:
        raise _err("Choose an X column and at least one Y column.", "Choisissez une colonne X et au moins une colonne Y.")
    excl = excluded_row_ids(content)
    series = []
    for yc in ycols:
        col = col_by_id(content, yc)
        if col is None:
            continue
        pts = []
        for row in content["rows"]:
            if row["id"] in excl:
                continue
            x, y = row["cells"].get(xc), row["cells"].get(yc)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x is not None and y is not None:
                if (cfg["x_scale"] == "log10" and x <= 0) or (cfg["y_scale"] == "log10" and y <= 0):
                    continue
                pts.append((float(x), float(y)))
        series.append((col["name"], pts))
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
    for v in xticks:
        x = xs(v)
        svg.line(x, bottom, x, bottom + 3, width=lw)
        svg.text(x, bottom + 4.5 + fs, fmt_tick(v))
    svg.add(f'<clipPath id="plotclip"><rect x="{left:.2f}" y="{top - 1:.2f}" width="{right - left:.2f}" height="{bottom - top + 1:.2f}"/></clipPath>')
    svg.add('<g clip-path="url(#plotclip)">')
    counts = []
    for i, (name, pts) in enumerate(series):
        color = color_for(cfg, name, i)
        counts.append((name, len(pts)))
        if t == "xy":
            for x, y in pts:
                svg.marker(xs(x), ys(y), color)
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
                for x, y in pts:
                    svg.marker(xs(x), ys(y), color, size=cfg["point_size_pt"] * 0.6, opacity=0.4)
    svg.add("</g>")
    if (cfg["legend"] == "auto" and len(series) > 1) or cfg["legend"] is True:
        ly = top + 2
        for i, (name, _) in enumerate(series):
            color = color_for(cfg, name, i)
            lx = right - max(text_width(n, fs) for n, _ in series) - 14
            svg.marker(lx, ly + fs * 0.5, color)
            svg.text(lx + 6, ly + fs * 0.85, name, anchor="start")
            ly += fs * 1.4
    return caption(cfg, counts, lang)


def to_pdf(svg: str) -> bytes:
    import cairosvg

    return cairosvg.svg2pdf(bytestring=svg.encode())


def to_png(svg: str, dpi: int) -> bytes:
    import cairosvg

    return cairosvg.svg2png(bytestring=svg.encode(), dpi=dpi)
