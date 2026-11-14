import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, Cell, Column, Dataset, Design, TableContent } from "../api";
import { useI18n } from "../i18n";
import type { ProjectCtx } from "../pages/ProjectPage";

const MISSING = new Set(["", "na", "n/a", "nan", "null", "-", "--", "?", "nd", "n.d.", "#n/a"]);

/** Parse a typed/pasted cell. Accepts 1.5 and 1,5. Returns null for missing (never 0). */
export function parseCell(s: string, type: Column["type"]): Cell {
  const v = s.trim();
  if (MISSING.has(v.toLowerCase())) return null;
  if (type === "text") return v;
  const norm = /^[+-]?\d*,\d+([eE][+-]?\d+)?$/.test(v) ? v.replace(",", ".") : v.replace(/[\s  ]/g, "");
  if (/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(norm)) return Number(norm);
  return v; // kept as typed and flagged invalid
}

export function displayCell(v: Cell, lang: string): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return lang === "fr" ? String(v).replace(".", ",") : String(v);
  return v;
}

let idCounter = Date.now() % 100000;
const newId = (p: string, existing: Set<string>) => {
  let id;
  do id = `${p}${(idCounter++).toString(36)}`;
  while (existing.has(id));
  return id;
};

type SaveState = "saved" | "dirty" | "saving" | "conflict" | "error";

export default function DataView({ ctx }: { ctx: ProjectCtx }) {
  const { datasetId } = useParams();
  const [search] = useSearchParams();
  const flashRow = search.get("row");
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const pid = ctx.project.id;
  const canEdit = ctx.project.permissions.includes("edit");
  const [ds, setDs] = useState<Dataset | null>(null);
  const [content, setContent] = useState<TableContent | null>(null);
  const [name, setName] = useState("");
  const [save, setSave] = useState<SaveState>("saved");
  const [editing, setEditing] = useState<{ r: string; c: string; text: string } | null>(null);
  const [versions, setVersions] = useState<{ version: number; change_note: string; created_at: string }[] | null>(null);
  const versionRef = useRef(0);
  const pendingRef = useRef<{ content?: TableContent; name?: string } | null>(null);
  const timer = useRef<number | undefined>();
  const tableRef = useRef<HTMLTableElement>(null);

  useEffect(() => {
    if (!flashRow || !content) return;
    const el = tableRef.current?.querySelector(`tr[data-rowid="${CSS.escape(flashRow)}"]`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [flashRow, !!content]);

  useEffect(() => {
    setDs(null);
    api.get<Dataset>(`/api/projects/${pid}/datasets/${datasetId}`).then((d) => {
      setDs(d);
      setContent(d.content);
      setName(d.name);
      versionRef.current = d.version;
      setSave("saved");
    });
    return () => window.clearTimeout(timer.current);
  }, [pid, datasetId]);

  const flush = useCallback(async () => {
    const p = pendingRef.current;
    if (!p) return;
    pendingRef.current = null;
    setSave("saving");
    try {
      const r = await api.put<{ version: number }>(`/api/projects/${pid}/datasets/${datasetId}`, { ...p, version: versionRef.current, change_note: "edit" });
      versionRef.current = r.version;
      setSave(pendingRef.current ? "dirty" : "saved");
      ctx.reload();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setSave("conflict");
      else {
        pendingRef.current = { ...p, ...(pendingRef.current || {}) };
        setSave("error");
      }
    }
  }, [pid, datasetId, ctx]);

  // flush on unmount / page leave
  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => { if (pendingRef.current) { e.preventDefault(); } };
    window.addEventListener("beforeunload", h);
    return () => { window.removeEventListener("beforeunload", h); flush(); };
  }, [flush]);

  function schedule(patch: { content?: TableContent; name?: string }) {
    pendingRef.current = { ...(pendingRef.current || {}), ...patch };
    setSave("dirty");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, 700);
  }

  function update(fn: (c: TableContent) => TableContent) {
    setContent((prev) => {
      if (!prev) return prev;
      const next = fn(structuredClone(prev));
      schedule({ content: next });
      return next;
    });
  }

  const excluded = useMemo(() => new Map((content?.exclusions || []).map((e) => [e.row_id, e.reason])), [content]);
  const roleOf = useMemo(() => {
    const m = new Map<string, string>();
    const d = content?.design;
    if (!d) return m;
    if (d.layout === "wide") (d.group_columns || []).forEach((c) => m.set(c, t("group")));
    else {
      if (d.group) m.set(d.group, t("group"));
      if (d.value) m.set(d.value, t("valueCol").split(" ").pop() || "");
    }
    if (d.subject) m.set(d.subject, lang === "fr" ? "sujet" : "subject");
    if (d.technical) m.set(d.technical, "tech");
    if (d.time) m.set(d.time, t("timeCol"));
    if (d.condition) m.set(d.condition, t("conditionCol"));
    return m;
  }, [content, t]);

  if (!ds || !content) return null;

  function commitCell(rowId: string, col: Column, text: string) {
    const v = parseCell(text, col.type);
    update((c) => {
      const row = c.rows.find((r) => r.id === rowId);
      if (row) row.cells[col.id] = v;
      return c;
    });
  }

  function onPaste(e: React.ClipboardEvent, rowIdx: number, colIdx: number) {
    const txt = e.clipboardData.getData("text/plain");
    if (!txt.includes("\t") && !txt.includes("\n")) return; // single value: let the input handle it
    e.preventDefault();
    const lines = txt.replace(/\r/g, "").split("\n");
    while (lines.length && lines[lines.length - 1] === "") lines.pop();
    const matrix = lines.map((l) => l.split("\t"));
    setEditing(null);
    update((c) => {
      const width = Math.max(...matrix.map((r) => r.length));
      const colIds = new Set(c.columns.map((x) => x.id));
      while (c.columns.length < colIdx + width) {
        const id = newId("c", colIds);
        colIds.add(id);
        c.columns.push({ id, name: `Col ${c.columns.length + 1}`, type: "numeric" });
        if (c.design.layout === "wide") c.design.group_columns = [...(c.design.group_columns || []), id];
      }
      const rowIds = new Set(c.rows.map((r) => r.id));
      while (c.rows.length < rowIdx + matrix.length) {
        const id = newId("r", rowIds);
        rowIds.add(id);
        c.rows.push({ id, cells: {} });
      }
      matrix.forEach((vals, i) => vals.forEach((s, j) => {
        const col = c.columns[colIdx + j];
        c.rows[rowIdx + i].cells[col.id] = parseCell(s, col.type);
      }));
      // Infer column types: a numeric column whose values are all text (e.g. genotype names) becomes text.
      for (let j = 0; j < width; j++) {
        const col = c.columns[colIdx + j];
        const vals = c.rows.map((r) => r.cells[col.id]).filter((v) => v !== null && v !== undefined);
        if (col.type === "numeric" && vals.length && vals.every((v) => typeof v === "string")) {
          col.type = "text";
          if (c.design.group_columns) c.design.group_columns = c.design.group_columns.filter((x) => x !== col.id);
        }
      }
      // a pasted text value in a numeric column switches the column to text only if no value is numeric
      return c;
    });
  }

  function focusCell(r: number, c: number) {
    const el = tableRef.current?.querySelector<HTMLInputElement>(`input[data-r="${r}"][data-c="${c}"]`);
    el?.focus();
    el?.select();
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>, r: number, c: number) {
    if (e.key === "Enter" || (e.key === "ArrowDown" && !e.altKey)) { e.preventDefault(); (e.target as HTMLInputElement).blur(); focusCell(r + (e.shiftKey ? -1 : 1), c); }
    else if (e.key === "ArrowUp") { e.preventDefault(); (e.target as HTMLInputElement).blur(); focusCell(r - 1, c); }
    else if (e.key === "Tab") { /* native */ }
    else if (e.key === "Escape") { setEditing(null); (e.target as HTMLInputElement).blur(); }
  }

  function setDesign(patch: Partial<Design>) {
    update((c) => ({ ...c, design: { ...c.design, ...patch } }));
  }

  function toggleExclusion(rowId: string) {
    if (excluded.has(rowId)) {
      update((c) => ({ ...c, exclusions: c.exclusions.filter((e) => e.row_id !== rowId) }));
    } else {
      const reason = window.prompt(t("excludeReason"));
      if (!reason || !reason.trim()) return;
      update((c) => ({ ...c, exclusions: [...c.exclusions, { row_id: rowId, reason: reason.trim(), at: new Date().toISOString() }] }));
    }
  }

  const design = content.design;
  const numericCols = content.columns.filter((c) => c.type === "numeric");
  const colSelect = (value: string | null | undefined, onChange: (v: string | null) => void, cols = content.columns) => (
    <select value={value || ""} onChange={(e) => onChange(e.target.value || null)} disabled={!canEdit}>
      <option value="">{t("none")}</option>
      {cols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select>
  );

  const saveLabel = { saved: t("saved"), dirty: t("unsaved"), saving: t("saving"), conflict: t("error"), error: t("error") }[save];

  return (
    <>
      <div className="main-header">
        <input className="name-input" value={name} disabled={!canEdit} aria-label={t("tableName")}
          onChange={(e) => setName(e.target.value)} onBlur={() => name.trim() && name !== ds.name && schedule({ name })} />
        <span className="badge">{t("version")} {versionRef.current}</span>
        <span className={`small-text ${save === "saved" ? "muted" : ""}`} aria-live="polite">{saveLabel}</span>
        <div className="grow" />
        <button className="small" onClick={async () => setVersions(versions ? null : await api.get(`/api/projects/${pid}/datasets/${datasetId}/versions`))}>{t("versionHistory")}</button>
        <a className="btn small" href={`/api/projects/${pid}/datasets/${datasetId}/export.csv?decimal=${lang === "fr" ? "," : "."}`}>{t("exportCsv")}</a>
        {canEdit && <button className="small danger" onClick={async () => {
          if (!window.confirm(t("confirmDelete"))) return;
          await api.del(`/api/projects/${pid}/datasets/${datasetId}`);
          await ctx.reload();
          nav(`/p/${pid}`);
        }}>{t("delete")}</button>}
      </div>
      {save === "conflict" && <div className="notice error">{t("conflict")} <button className="small" onClick={() => window.location.reload()}>{t("reload")}</button></div>}
      {versions && (
        <div className="panel small-text">
          <h3>{t("versionHistory")}</h3>
          {versions.map((v) => <div key={v.version}>v{v.version} · {new Date(v.created_at).toLocaleString()} · <span className="muted">{v.change_note}</span></div>)}
        </div>
      )}

      <details className="panel" open={!design.group_columns && design.layout === "wide" ? false : undefined}>
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>{t("design")}</summary>
        <div style={{ marginTop: 10 }}>
          <label className="field"><span>{t("layout")}</span>
            <select value={design.layout} disabled={!canEdit} onChange={(e) => setDesign({ layout: e.target.value as Design["layout"] })}>
              <option value="wide">{t("wide")}</option><option value="long">{t("long")}</option>
            </select>
          </label>
          {design.layout === "wide" ? (
            <>
              <div className="field"><span>{t("groupColumns")}</span>
                <div className="row">
                  {numericCols.map((c) => (
                    <label key={c.id} className="check" style={{ marginBottom: 0 }}>
                      <input type="checkbox" disabled={!canEdit} checked={(design.group_columns || numericCols.map((x) => x.id)).includes(c.id)}
                        onChange={(e) => {
                          const cur = design.group_columns || numericCols.map((x) => x.id);
                          setDesign({ group_columns: e.target.checked ? [...cur, c.id] : cur.filter((x) => x !== c.id) });
                        }} /> {c.name}
                    </label>
                  ))}
                </div>
              </div>
              <div className="grid2">
                <label className="field"><span>{t("subjectCol")}</span>{colSelect(design.subject, (v) => setDesign({ subject: v }))}</label>
              </div>
              <p className="small-text muted">{t("wideSubjectHelp")}</p>
            </>
          ) : (
            <div className="grid3">
              <label className="field"><span>{t("groupCol")}</span>{colSelect(design.group, (v) => setDesign({ group: v }))}</label>
              <label className="field"><span>{t("valueCol")}</span>{colSelect(design.value, (v) => setDesign({ value: v }), numericCols)}</label>
              <label className="field"><span>{t("subjectCol")}</span>{colSelect(design.subject, (v) => setDesign({ subject: v }))}</label>
              <label className="field"><span>{t("technicalCol")}</span>{colSelect(design.technical, (v) => setDesign({ technical: v }))}</label>
              <label className="field"><span>{t("timeCol")}</span>{colSelect(design.time, (v) => setDesign({ time: v }))}</label>
              <label className="field"><span>{t("conditionCol")}</span>{colSelect(design.condition, (v) => setDesign({ condition: v }))}</label>
            </div>
          )}
        </div>
      </details>

      <p className="small-text muted">{t("missingHelp")} {canEdit && t("pasteHelp")}</p>
      <div className="grid-wrap">
        <table className="grid" ref={tableRef}>
          <thead>
            <tr>
              <th className="rowhead">#</th>
              {content.columns.map((col) => (
                <th key={col.id}>
                  <div className="colhead">
                    <input value={col.name} disabled={!canEdit} aria-label={t("rename")}
                      onChange={(e) => { const v = e.target.value; update((c) => { c.columns.find((x) => x.id === col.id)!.name = v; return c; }); }} />
                    {roleOf.get(col.id) && <span className="role-tag">{roleOf.get(col.id)}</span>}
                    <select className="type" value={col.type} disabled={!canEdit} aria-label="type" style={{ border: "none", background: "transparent", padding: 0, fontSize: 10 }}
                      onChange={(e) => {
                        const type = e.target.value as Column["type"];
                        update((c) => {
                          const cc = c.columns.find((x) => x.id === col.id)!;
                          cc.type = type;
                          c.rows.forEach((r) => { const v = r.cells[col.id]; if (v !== null && v !== undefined) r.cells[col.id] = parseCell(String(v), type); });
                          return c;
                        });
                      }}>
                      <option value="numeric">123</option><option value="text">abc</option>
                    </select>
                    {canEdit && <button className="ghost small" title={t("deleteColumn")} aria-label={t("deleteColumn")} onClick={() => {
                      if (!window.confirm(`${t("deleteColumn")} « ${col.name} » ?`)) return;
                      update((c) => {
                        c.columns = c.columns.filter((x) => x.id !== col.id);
                        c.rows.forEach((r) => delete r.cells[col.id]);
                        const d = c.design;
                        if (d.group_columns) d.group_columns = d.group_columns.filter((x) => x !== col.id);
                        (["group", "value", "subject", "technical", "time", "condition"] as const).forEach((k) => { if (d[k] === col.id) d[k] = null; });
                        return c;
                      });
                    }}>×</button>}
                  </div>
                </th>
              ))}
              {canEdit && <th><button className="ghost small" onClick={() => update((c) => {
                const id = newId("c", new Set(c.columns.map((x) => x.id)));
                c.columns.push({ id, name: String.fromCharCode(65 + (c.columns.length % 26)), type: "numeric" });
                if (c.design.layout === "wide" && c.design.group_columns) c.design.group_columns.push(id);
                return c;
              })}>{t("addColumn")}</button></th>}
            </tr>
          </thead>
          <tbody>
            {content.rows.map((row, ri) => (
              <tr key={row.id} data-rowid={row.id} className={`${excluded.has(row.id) ? "excluded" : ""} ${row.id === flashRow ? "row-flash" : ""}`} title={excluded.get(row.id) || ""}>
                <td className="rowhead">
                  <span className="row tight" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                    {canEdit && <button className="ghost small" style={{ padding: "0 3px" }} title={excluded.has(row.id) ? t("include") : t("exclude")}
                      aria-label={excluded.has(row.id) ? t("include") : t("exclude")} onClick={() => toggleExclusion(row.id)}>{excluded.has(row.id) ? "↺" : "⊘"}</button>}
                    {ri + 1}
                  </span>
                </td>
                {content.columns.map((col, ci) => {
                  const v = row.cells[col.id];
                  const isEditing = editing && editing.r === row.id && editing.c === col.id;
                  const invalid = col.type === "numeric" && typeof v === "string";
                  return (
                    <td key={col.id} className={`${col.type === "numeric" ? "num" : ""} ${invalid ? "invalid" : ""} ${v === null || v === undefined ? "missing" : ""}`} title={invalid ? t("invalidNumber") : ""}>
                      <input data-r={ri} data-c={ci} readOnly={!canEdit} placeholder={col.type === "numeric" ? "·" : ""} aria-label={`${col.name} ${ri + 1}`}
                        value={isEditing ? editing.text : displayCell(v ?? null, lang)}
                        onFocus={(e) => { setEditing({ r: row.id, c: col.id, text: displayCell(v ?? null, lang) }); e.target.select(); }}
                        onChange={(e) => setEditing({ r: row.id, c: col.id, text: e.target.value })}
                        onBlur={() => { if (editing && editing.r === row.id && editing.c === col.id && editing.text !== displayCell(v ?? null, lang)) commitCell(row.id, col, editing.text); setEditing(null); }}
                        onKeyDown={(e) => onKey(e, ri, ci)}
                        onPaste={(e) => canEdit && onPaste(e, ri, ci)} />
                    </td>
                  );
                })}
                {canEdit && <td><button className="ghost small" title={t("deleteRow")} aria-label={t("deleteRow")} onClick={() => update((c) => {
                  c.rows = c.rows.filter((r) => r.id !== row.id);
                  c.exclusions = c.exclusions.filter((e) => e.row_id !== row.id);
                  return c;
                })}>×</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canEdit && <div className="row" style={{ marginTop: 8 }}>
        <button className="small" onClick={() => update((c) => { c.rows.push({ id: newId("r", new Set(c.rows.map((r) => r.id))), cells: {} }); return c; })}>{t("addRow")}</button>
        <span className="muted small-text">{content.rows.length} {t("rows")} × {content.columns.length} {t("columns")}</span>
        <span className="badge soon">{t("transformations")} — {t("comingSoon")}</span>
      </div>}
      {content.exclusions.length > 0 && (
        <div className="panel" style={{ marginTop: 14 }}>
          <h3>{t("exclusions")} ({content.exclusions.length})</h3>
          <p className="small-text muted">{t("exclusionsHelp")}</p>
          <table className="stats"><tbody>
            {content.exclusions.map((e) => {
              const idx = content.rows.findIndex((r) => r.id === e.row_id);
              return (
                <tr key={e.row_id}>
                  <td>#{idx + 1}</td><td style={{ textAlign: "left" }}>{e.reason}</td>
                  <td className="muted small-text">{e.at ? new Date(e.at).toLocaleString() : ""}</td>
                  <td>{canEdit && <button className="small" onClick={() => toggleExclusion(e.row_id)}>{t("include")}</button>}</td>
                </tr>
              );
            })}
          </tbody></table>
        </div>
      )}
    </>
  );
}
