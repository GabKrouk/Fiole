"""Extract analysis-ready values from a dataset's content according to its design.

Dataset content:
{
  "columns": [{"id", "name", "type": "numeric"|"text"}],
  "rows": [{"id", "cells": {col_id: number|string|null}}],
  "design": {
      "layout": "wide" | "long",
      "group_columns": [col_id, ...],   # wide: each column is one group
      "group": col_id, "value": col_id, # long
      "subject": col_id | null,         # experimental unit / biological replicate (pairing key)
      "technical": col_id | null,       # technical replicate label (long)
      "time": col_id | null, "condition": col_id | null,
      "group_order": [name, ...]        # optional display order
  },
  "exclusions": [{"row_id", "reason", "at"}]
}
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field

from .i18n import msg


@dataclass
class Group:
    name: str
    values: list[float] = field(default_factory=list)
    units: list[str | None] = field(default_factory=list)  # subject / experimental unit label per value
    row_ids: list[str] = field(default_factory=list)
    n_missing: int = 0
    n_excluded: int = 0
    n_technical_averaged: int = 0  # number of raw values merged by technical-replicate averaging


class DataError(Exception):
    def __init__(self, message: dict):
        super().__init__(message.get("en"))
        self.message = message


def col_by_id(content: dict, cid: str | None) -> dict | None:
    if not cid:
        return None
    for c in content.get("columns", []):
        if c["id"] == cid:
            return c
    return None


def excluded_row_ids(content: dict) -> set[str]:
    return {e["row_id"] for e in content.get("exclusions", [])}


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    return None


def _label(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def design_for(content: dict, config: dict | None) -> dict:
    """Column-driven override: if a figure/analysis picks a group (factor) column and a
    value column, use them instead of the table's saved layout."""
    base = dict(content.get("design") or {})
    config = config or {}
    if config.get("value_col"):
        if config.get("group_col"):
            out = {"layout": "long", "group": config["group_col"], "value": config["value_col"],
                   "subject": config.get("subject_col") or (base.get("subject") if base.get("subject") != config["group_col"] else None)}
        else:
            # no factor: a single group made of the whole column
            out = {"layout": "wide", "group_columns": [config["value_col"]], "subject": config.get("subject_col") or base.get("subject")}
        return out
    return base


def extract_groups(content: dict, design: dict | None = None) -> "OrderedDict[str, Group]":
    design = design or content.get("design") or {}
    layout = design.get("layout", "wide")
    excl = excluded_row_ids(content)
    subject_col = design.get("subject")
    groups: "OrderedDict[str, Group]" = OrderedDict()

    if layout == "wide":
        gcols = design.get("group_columns") or [c["id"] for c in content["columns"] if c.get("type") == "numeric" and c["id"] != subject_col]
        for cid in gcols:
            col = col_by_id(content, cid)
            if col is None:
                continue
            g = Group(name=col["name"])
            for i, row in enumerate(content["rows"]):
                raw = row["cells"].get(cid)
                if row["id"] in excl:
                    if raw is not None:
                        g.n_excluded += 1
                    continue
                v = _num(raw)
                if v is None:
                    if raw is None or isinstance(raw, str):
                        g.n_missing += 1
                    continue
                g.values.append(v)
                g.units.append(_label(row["cells"].get(subject_col)) if subject_col else f"row {i + 1}")
                g.row_ids.append(row["id"])
            groups[g.name] = g
    else:
        gcol, vcol = design.get("group"), design.get("value")
        if not gcol or not vcol:
            raise DataError(msg("design_incomplete_long"))
        for row in content["rows"]:
            gname = _label(row["cells"].get(gcol))
            if gname is None:
                continue
            g = groups.setdefault(gname, Group(name=gname))
            raw = row["cells"].get(vcol)
            if row["id"] in excl:
                if raw is not None:
                    g.n_excluded += 1
                continue
            v = _num(raw)
            if v is None:
                g.n_missing += 1
                continue
            g.values.append(v)
            g.units.append(_label(row["cells"].get(subject_col)) if subject_col else None)
            g.row_ids.append(row["id"])

    order = design.get("group_order")
    if order:
        ordered = OrderedDict((k, groups[k]) for k in order if k in groups)
        for k, v in groups.items():
            ordered.setdefault(k, v)
        groups = ordered
    return groups


def average_technical(g: Group) -> Group:
    """Average values within each experimental unit (subject). Requires unit labels."""
    if any(u is None for u in g.units):
        raise DataError(msg("technical_needs_subject"))
    acc: "OrderedDict[str, list[float]]" = OrderedDict()
    for u, v in zip(g.units, g.values):
        acc.setdefault(u, []).append(v)
    out = Group(name=g.name, n_missing=g.n_missing, n_excluded=g.n_excluded)
    for u, vals in acc.items():
        out.values.append(sum(vals) / len(vals))
        out.units.append(u)
        out.row_ids.append("")
    out.n_technical_averaged = len(g.values)
    return out


def pair_groups(a: Group, b: Group) -> tuple[list[float], list[float], list[str], list[dict]]:
    """Match two groups by experimental unit. Returns (x, y, units, warnings)."""
    warnings: list[dict] = []
    if any(u is None for u in a.units + b.units):
        raise DataError(msg("pairing_needs_subject"))
    da: dict[str, float] = {}
    db_: dict[str, float] = {}
    for src, dst, g in ((a, da, a), (b, db_, b)):
        for u, v in zip(src.units, src.values):
            if u in dst:
                raise DataError(msg("pairing_duplicate_unit", group=g.name, unit=u))
            dst[u] = v
    common = [u for u in da if u in db_]
    unmatched = [u for u in da if u not in db_] + [u for u in db_ if u not in da]
    if unmatched:
        warnings.append(msg("pairing_unmatched", count=len(unmatched), units=", ".join(unmatched[:5])))
    return [da[u] for u in common], [db_[u] for u in common], common, warnings


def extract_xy(content: dict, x_col: str, y_col: str) -> tuple[list[float], list[float], list[str], int, int]:
    """Return (x, y, row_ids, n_missing_pairs, n_excluded)."""
    excl = excluded_row_ids(content)
    xs, ys, ids = [], [], []
    n_missing = n_excl = 0
    for row in content["rows"]:
        if row["id"] in excl:
            n_excl += 1
            continue
        x, y = _num(row["cells"].get(x_col)), _num(row["cells"].get(y_col))
        if x is None or y is None:
            if row["cells"].get(x_col) is not None or row["cells"].get(y_col) is not None:
                n_missing += 1
            continue
        xs.append(x)
        ys.append(y)
        ids.append(row["id"])
    return xs, ys, ids, n_missing, n_excl
