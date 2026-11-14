import type { Column, TableContent } from "./api";

/** A column is a factor (categorical) if it is typed text or holds any text value. */
export function isFactor(c: TableContent, col: Column): boolean {
  if (col.type === "text") return true;
  return c.rows.some((r) => typeof r.cells[col.id] === "string");
}

export function factorColumns(c: TableContent | undefined): Column[] {
  return c ? c.columns.filter((col) => isFactor(c, col)) : [];
}

export function numericColumns(c: TableContent | undefined): Column[] {
  return c ? c.columns.filter((col) => !isFactor(c, col) && c.rows.some((r) => typeof r.cells[col.id] === "number")) : [];
}

export function levels(c: TableContent, colId: string): string[] {
  const out: string[] = [];
  for (const r of c.rows) {
    const v = r.cells[colId];
    if (v === null || v === undefined || v === "") continue;
    const s = String(v);
    if (!out.includes(s)) out.push(s);
  }
  return out;
}

/** Sensible first choice: first factor column as groups, first numeric column as values. */
export function smartDefaults(c: TableContent | undefined): { group_col?: string; value_col?: string } {
  const f = factorColumns(c);
  const n = numericColumns(c);
  if (!f.length || !n.length) return {};
  return { group_col: f[0].id, value_col: n[0].id };
}
