"""Import of CSV / TSV / pasted text / Excel into the dataset table format.

Rules:
* Missing-value tokens become `None`. Nothing is ever converted to 0.
* Decimal separator (',' or '.') and delimiter are detected, reported, and can
  be overridden by the user before validation.
* Everything returned here is a *preview*; nothing is stored until confirmed.
"""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, datetime, time
from typing import Any

MISSING_TOKENS = {
    "", "na", "n/a", "nan", "null", "none", "-", "--", "?", ".", "#n/a", "#na", "nd", "n.d.", "#div/0!", "#value!",
}

_DOT_NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_COMMA_NUM = re.compile(r"^[+-]?(\d+(,\d*)?|,\d+)([eE][+-]?\d+)?$")
_THOUSANDS_SPACE = re.compile(r"^[+-]?\d{1,3}([   ]\d{3})+([.,]\d+)?$")

DELIMITERS = ["\t", ";", ",", "|"]


def is_missing(s: str) -> bool:
    return s.strip().lower() in MISSING_TOKENS


def parse_number(s: str, decimal: str) -> float | None:
    """Return a float, or None if `s` is not a number in the given decimal convention."""
    t = s.strip()
    if _THOUSANDS_SPACE.match(t):
        t = re.sub(r"[   ]", "", t)
    if decimal == ",":
        if _COMMA_NUM.match(t):
            v = float(t.replace(",", "."))
            return v if math.isfinite(v) else None
        if _DOT_NUM.match(t) and "." not in t:  # plain integers
            return float(t)
        return None
    if _DOT_NUM.match(t):
        v = float(t)
        return v if math.isfinite(v) else None
    return None


def detect_delimiter(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()][:50]
    if not lines:
        return ","
    best, best_score = ",", -1.0
    for d in DELIMITERS:
        counts = [len(r) for r in csv.reader(lines, delimiter=d)]
        if not counts or max(counts) < 2:
            continue
        mode = max(set(counts), key=counts.count)
        consistency = counts.count(mode) / len(counts)
        score = consistency * 10 + min(mode, 50) / 50
        if d == "\t":
            score += 0.2  # spreadsheet pastes are tab-separated
        if score > best_score:
            best, best_score = d, score
    return best


def detect_decimal(cells: list[str], delimiter: str) -> str:
    comma = dot = 0
    for c in cells:
        t = c.strip()
        if re.match(r"^[+-]?\d*,\d+$", t):
            comma += 1
        elif re.match(r"^[+-]?\d*\.\d+([eE][+-]?\d+)?$", t):
            dot += 1
    if delimiter == "," and comma == 0:
        return "."
    return "," if comma > dot else "."


def _rows_from_text(text: str, delimiter: str) -> list[list[str]]:
    text = text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    # Drop fully empty trailing rows
    while rows and all(not c.strip() for c in rows[-1]):
        rows.pop()
    return rows


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _cell_to_str(v: Any) -> str | float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    if isinstance(v, datetime):
        return v.isoformat(sep=" ") if (v.hour or v.minute or v.second) else v.date().isoformat()
    if isinstance(v, (date, time)):
        return v.isoformat()
    return str(v)


def build_table(raw: list[list[Any]], header: bool, decimal: str) -> dict:
    """raw cells may be str (text import) or float/None (already-typed Excel cells)."""
    width = max((len(r) for r in raw), default=0)
    raw = [list(r) + [None] * (width - len(r)) for r in raw]
    # Remove fully empty columns at the right end
    while width > 0 and all((r[width - 1] is None or (isinstance(r[width - 1], str) and not r[width - 1].strip())) for r in raw):
        width -= 1
        raw = [r[:width] for r in raw]
    if header and raw:
        names = [str(c).strip() if c not in (None, "") else "" for c in raw[0]]
        body = raw[1:]
    else:
        names = [""] * width
        body = raw
    seen: dict[str, int] = {}
    columns = []
    for i, n in enumerate(names):
        n = n or f"Col {i + 1}"
        if n in seen:
            seen[n] += 1
            n = f"{n} ({seen[n]})"
        else:
            seen[n] = 1
        columns.append({"id": f"c{i + 1}", "name": n, "type": "numeric"})

    warnings: list[dict] = []
    missing_count = 0
    parsed: list[list[Any]] = []
    for r in body:
        out = []
        for v in r:
            if v is None or (isinstance(v, str) and is_missing(v)):
                out.append(None)
                missing_count += 1
            elif isinstance(v, float):
                out.append(v)
            else:
                num = parse_number(str(v), decimal)
                out.append(num if num is not None else str(v).strip())
        parsed.append(out)

    for j, col in enumerate(columns):
        vals = [row[j] for row in parsed if row[j] is not None]
        non_num = [v for v in vals if isinstance(v, str)]
        if vals and non_num:
            col["type"] = "text"
            if len(non_num) < len(vals):
                warnings.append(
                    {
                        "code": "mixed_column",
                        "column": col["name"],
                        "count": len(non_num),
                        "examples": non_num[:3],
                        "en": f"Column “{col['name']}” mixes numbers and text ({len(non_num)} non-numeric value(s), e.g. {', '.join(map(repr, non_num[:3]))}). It was imported as text; fix the cells to use it as numbers.",
                        "fr": f"La colonne « {col['name']} » mélange nombres et texte ({len(non_num)} valeur(s) non numérique(s), ex. {', '.join(map(repr, non_num[:3]))}). Elle est importée comme texte ; corrigez les cellules pour l’utiliser comme numérique.",
                    }
                )
        if col["type"] == "text":
            for row in parsed:
                if isinstance(row[j], float):
                    row[j] = _fmt_num(row[j])

    rows = [{"id": f"r{i + 1}", "cells": {columns[j]["id"]: row[j] for j in range(width)}} for i, row in enumerate(parsed)]
    return {
        "columns": columns,
        "rows": rows,
        "missing_count": missing_count,
        "warnings": warnings,
    }


def _fmt_num(v: float) -> str:
    return str(int(v)) if v.is_integer() else repr(v)


def parse_text(text: str, delimiter: str | None = None, decimal: str | None = None, header: bool | None = None) -> dict:
    d = delimiter or detect_delimiter(text)
    raw = _rows_from_text(text, d)
    flat = [c for r in raw[1:200] for c in r]
    dec = decimal or detect_decimal(flat, d)
    if header is None:
        first = raw[0] if raw else []
        header = not (first and all(parse_number(c, dec) is not None or is_missing(c) for c in first))
    table = build_table(raw, header, dec)
    table["detected"] = {"delimiter": d, "decimal": dec, "header": header}
    return table


def excel_sheets(data: bytes) -> list[str]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    names = wb.sheetnames
    wb.close()
    return names


def parse_excel(data: bytes, sheet: str | None = None, header: bool | None = None, decimal: str | None = None) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheets = wb.sheetnames
    ws = wb[sheet] if sheet in sheets else wb[sheets[0]]
    raw = [[_cell_to_str(v) for v in row] for row in ws.iter_rows(values_only=True)]
    wb.close()
    while raw and all(v is None or (isinstance(v, str) and not v.strip()) for v in raw[-1]):
        raw.pop()
    str_cells = [v for r in raw[1:200] for v in r if isinstance(v, str)]
    dec = decimal or detect_decimal(str_cells, ";")
    if header is None:
        first = raw[0] if raw else []
        header = not (first and all(isinstance(v, float) or v is None for v in first))
    table = build_table(raw, header, dec)
    table["detected"] = {"delimiter": None, "decimal": dec, "header": header, "sheet": ws.title, "sheets": sheets}
    return table
