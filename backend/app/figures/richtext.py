"""Rich text for figure labels: a small, safe markup and an exact layout engine.

Markup (what the editor stores): <b>, <i>, <u>, <sup>, <sub> and <span color="#rrggbb">, nestable.
Anything else is literal text. Entities (&lt; &amp; …) are decoded.

Why lay out runs ourselves: CairoSVG (PDF/PNG export) ignores text-anchor for multi-<tspan> text, misplaces
shifted tspans and drops underlines, so styled text exported differently from the preview. Here each run is
measured with the real font metrics (Liberation fonts are metric-compatible with Arial / Times New Roman, the
fonts the browser uses) and drawn as its own <text> at an absolute x: the browser and CairoSVG then agree.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, replace
from functools import lru_cache

TAG_RE = re.compile(r'<(/?)(b|i|u|sup|sub)>|<span color="(#[0-9a-fA-F]{3,8})">|</span>')

FONT_FILES = {
    # family -> (regular, bold, italic, bold italic)
    "sans": ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
    "serif": ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf", "LiberationSerif-BoldItalic.ttf"),
    "dejavu": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
}
FONT_DIRS = ["/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/liberation2", "/usr/share/fonts/truetype/dejavu",
             "/usr/share/fonts/liberation", "/usr/share/fonts/dejavu"]


def family_key(font_family: str) -> str:
    f = (font_family or "").lower()
    if "times" in f or "serif" in f and "sans" not in f:
        return "serif"
    if "dejavu" in f:
        return "dejavu"
    return "sans"


@lru_cache(maxsize=32)
def _font(key: str, bold: bool, italic: bool):
    try:
        from PIL import ImageFont
    except Exception:  # pragma: no cover
        return None
    files = FONT_FILES.get(key, FONT_FILES["sans"])
    name = files[(1 if bold else 0) + (2 if italic else 0)]
    for cand in (name, files[1 if bold else 0], files[0]):
        for d in FONT_DIRS:
            p = os.path.join(d, cand)
            if os.path.exists(p):
                return ImageFont.truetype(p, 100)
    return None


def measure(text: str, size: float, bold: bool = False, italic: bool = False, family: str = "sans") -> float:
    """Advance width in pt of `text` at `size` pt."""
    if not text:
        return 0.0
    f = _font(family, bold, italic)
    if f is None:
        return 0.55 * size * len(text) * (1.07 if bold else 1.0)
    return f.getlength(text) * size / 100.0


@dataclass(frozen=True)
class Style:
    b: bool = False
    i: bool = False
    u: bool = False
    shift: str = ""  # "", "sup" or "sub"
    color: str | None = None


def parse(s: str) -> list[tuple[str, Style]]:
    """Split markup into (text, style) runs. Unknown tags stay literal."""
    runs: list[tuple[str, Style]] = []
    stack: list[tuple[str, Style]] = [("", Style())]
    pos = 0
    for m in TAG_RE.finditer(s):
        if m.start() > pos:
            runs.append((html.unescape(s[pos:m.start()]), stack[-1][1]))
        pos = m.end()
        cur = stack[-1][1]
        tag = m.group(0)
        if m.group(3):
            stack.append(("span", replace(cur, color=m.group(3))))
        elif tag == "</span>" or m.group(1):
            name = "span" if tag == "</span>" else m.group(2)
            for k in range(len(stack) - 1, 0, -1):  # close the innermost matching tag
                if stack[k][0] == name:
                    del stack[k:]
                    break
        else:
            name = m.group(2)
            st = {"b": replace(cur, b=True), "i": replace(cur, i=True), "u": replace(cur, u=True),
                  "sup": replace(cur, shift="sup"), "sub": replace(cur, shift="sub")}[name]
            stack.append((name, st))
    if pos < len(s):
        runs.append((html.unescape(s[pos:]), stack[-1][1]))
    return [(t, st) for t, st in runs if t]


def is_rich(s: str) -> bool:
    return bool(TAG_RE.search(s or ""))


def plain(s: str) -> str:
    return "".join(t for t, _ in parse(s or ""))


def run_size(size: float, st: Style) -> float:
    return size * 0.7 if st.shift else size


def width(s: str, size: float, bold: bool = False, family: str = "sans") -> float:
    return sum(measure(t, run_size(size, st), bold or st.b, st.i, family) for t, st in parse(s))
