import { useState } from "react";
import { api, ApiError, ImportPreview } from "../api";
import { useI18n } from "../i18n";
import Modal from "./Modal";

export default function ImportDialog({ projectId, onClose, onCreated }: { projectId: string; onClose: () => void; onCreated: (id: string) => void }) {
  const { t, L } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [delimiter, setDelimiter] = useState("");
  const [decimal, setDecimal] = useState("");
  const [header, setHeader] = useState<"auto" | "1" | "0">("auto");
  const [sheet, setSheet] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [name, setName] = useState("");
  const [layout, setLayout] = useState<"wide" | "long">("wide");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function runPreview(opts?: Partial<{ delimiter: string; decimal: string; header: string; sheet: string }>) {
    setError("");
    setBusy(true);
    const o = { delimiter, decimal, header, sheet, ...opts };
    try {
      let p: ImportPreview;
      if (preview?.upload_id && file) {
        p = await api.post<ImportPreview>(`/api/projects/${projectId}/uploads/${preview.upload_id}/reparse`, {
          delimiter: o.delimiter || null, decimal: o.decimal || null, header: o.header === "auto" ? "auto" : o.header === "1", sheet: o.sheet || null,
        });
      } else {
        const fd = new FormData();
        if (file) fd.append("file", file);
        else fd.append("text", text);
        if (o.delimiter) fd.append("delimiter", o.delimiter === "\t" ? "tab" : o.delimiter);
        if (o.decimal) fd.append("decimal", o.decimal);
        fd.append("header", o.header);
        if (o.sheet) fd.append("sheet", o.sheet);
        p = await api.form<ImportPreview>(`/api/projects/${projectId}/import/preview`, fd);
      }
      setPreview(p);
      if (!name) setName(file ? file.name.replace(/\.[^.]+$/, "") : t("table"));
      if (p.detected.sheet) setSheet(p.detected.sheet);
    } catch (e) {
      setError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!preview) return;
    const numeric = preview.columns.filter((c) => c.type === "numeric").map((c) => c.id);
    const text = preview.columns.filter((c) => c.type === "text").map((c) => c.id);
    const design =
      layout === "wide"
        ? { layout: "wide", group_columns: numeric, subject: text[0] ?? null }
        : { layout: "long", group: text[0] ?? null, value: numeric[0] ?? null, subject: text[1] ?? null };
    const d = await api.post<{ id: string }>(`/api/projects/${projectId}/datasets`, {
      name: name || t("table"),
      upload_id: preview.upload_id,
      content: { columns: preview.columns, rows: preview.rows, design, exclusions: [] },
    });
    onCreated(d.id);
  }

  const delimLabel = (d: string | null) => (d === "\t" ? "Tab" : d === ";" ? ";" : d === "," ? "," : d === "|" ? "|" : "—");

  return (
    <Modal title={t("importTitle")} onClose={onClose}
      footer={<>
        <button onClick={onClose}>{t("cancel")}</button>
        <button className="primary" disabled={!preview || busy} onClick={confirm}>{t("confirmImport")}</button>
      </>}>
      <div className="grid2">
        <div>
          <label className="field"><span>{t("chooseFile")}</span>
            <input type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); setSheet(""); }} />
          </label>
          <label className="field"><span>{t("orPaste")}</span>
            <textarea rows={6} value={text} disabled={!!file} onChange={(e) => { setText(e.target.value); setPreview(null); }} placeholder={"Contrôle\tTraité\n12,1\t16,0\n14,3\t13,1"} />
          </label>
          <button className="primary" disabled={busy || (!file && !text.trim())} onClick={() => runPreview()}>{t("preview")}</button>
        </div>
        <div>
          {preview && (
            <>
              <div className="grid2">
                {preview.detected.sheets ? (
                  <label className="field"><span>{t("sheet")}</span>
                    <select value={sheet} onChange={(e) => { setSheet(e.target.value); runPreview({ sheet: e.target.value }); }}>
                      {preview.detected.sheets.map((s) => <option key={s}>{s}</option>)}
                    </select>
                  </label>
                ) : (
                  <label className="field"><span>{t("delimiter")} ({t("detected")} : {delimLabel(preview.detected.delimiter)})</span>
                    <select value={delimiter} onChange={(e) => { setDelimiter(e.target.value); runPreview({ delimiter: e.target.value }); }}>
                      <option value="">{t("auto")}</option><option value={"\t"}>Tab</option><option value=";">;</option><option value=",">,</option><option value="|">|</option>
                    </select>
                  </label>
                )}
                <label className="field"><span>{t("decimal")} ({t("detected")} : « {preview.detected.decimal} »)</span>
                  <select value={decimal} onChange={(e) => { setDecimal(e.target.value); runPreview({ decimal: e.target.value }); }}>
                    <option value="">{t("auto")}</option><option value=",">, (1,5)</option><option value=".">. (1.5)</option>
                  </select>
                </label>
              </div>
              <label className="check"><input type="checkbox" checked={preview.detected.header} onChange={(e) => { const h = e.target.checked ? "1" : "0"; setHeader(h); runPreview({ header: h }); }} /> {t("header")}</label>
              <label className="field"><span>{t("tableName")}</span><input value={name} onChange={(e) => setName(e.target.value)} /></label>
              <label className="field"><span>{t("layout")}</span>
                <select value={layout} onChange={(e) => setLayout(e.target.value as "wide" | "long")}>
                  <option value="wide">{t("wide")}</option><option value="long">{t("long")}</option>
                </select>
              </label>
            </>
          )}
        </div>
      </div>
      {error && <div className="notice error">{error}</div>}
      {preview && (
        <>
          <p className="small-text" style={{ marginTop: 10 }}>
            {preview.rows.length} {t("rows")} × {preview.columns.length} {t("columns")} · <b>{preview.missing_count}</b> {t("missingFound")}.{" "}
            {preview.upload_id && <span className="muted">{t("rawKept")}</span>}
          </p>
          {preview.warnings.map((w, i) => <div key={i} className="notice warn">{L(w)}</div>)}
          <div className="preview-table">
            <table>
              <thead><tr>{preview.columns.map((c) => <th key={c.id}>{c.name} <span className="muted small-text">({c.type === "numeric" ? t("numeric") : t("text")})</span></th>)}</tr></thead>
              <tbody>
                {preview.rows.slice(0, 50).map((r) => (
                  <tr key={r.id}>{preview.columns.map((c) => {
                    const v = r.cells[c.id];
                    return v === null || v === undefined ? <td key={c.id} className="na">NA</td> : <td key={c.id}>{String(v)}</td>;
                  })}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}
