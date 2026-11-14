"""Named figure styles: what a style holds, and who may see it.

A style is appearance only. Data-dependent settings (columns, groups, axis ranges, titles, annotations,
size) are never part of a style, so applying one never changes what a figure shows.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import FigureStyle, FigureStyleShare, StyleVisibility, User

STYLE_KEYS = (
    "font_family", "font_size_pt", "title_size_pt", "line_width_pt", "point_size_pt", "point_shape", "point_opacity",
    "palette", "colors", "fill_opacity", "show_points", "jitter", "legend", "legend_dx", "legend_dy", "regression_line",
    "background", "frame", "tick_direction", "tick_length_pt", "minor_ticks", "p_format", "star_thresholds", "hide_ns",
)
FILE_FORMAT = "fiole-style"
FILE_VERSION = 1


def extract(cfg: dict) -> dict:
    """Keep only the appearance keys of a figure configuration."""
    return {k: cfg[k] for k in STYLE_KEYS if k in (cfg or {}) and cfg[k] is not None}


def can_view(db: Session, user: User, st: FigureStyle) -> bool:
    if st.owner_id == user.id or st.visibility == StyleVisibility.public:
        return True
    if st.visibility == StyleVisibility.shared:
        return db.execute(select(FigureStyleShare).where(FigureStyleShare.style_id == st.id, FigureStyleShare.user_id == user.id)).first() is not None
    return False


def style_out(st: FigureStyle, user: User, owner_name: str | None = None) -> dict:
    return {"id": st.id, "name": st.name, "description": st.description, "settings": st.settings,
            "visibility": st.visibility.value, "mine": st.owner_id == user.id, "owner": owner_name,
            "source_style_id": st.source_style_id, "version": st.version,
            "created_at": st.created_at.isoformat(), "updated_at": st.updated_at.isoformat()}


def to_file(st: FigureStyle) -> dict:
    return {"format": FILE_FORMAT, "format_version": FILE_VERSION, "name": st.name, "description": st.description, "settings": st.settings}
