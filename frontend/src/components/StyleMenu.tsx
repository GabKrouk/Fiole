import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { useI18n } from "../i18n";
import Modal from "./Modal";

type Cfg = Record<string, any>;

export interface FigureStyleItem {
  id: string;
  name: string;
  description: string;
  settings: Cfg;
  visibility: "private" | "shared" | "public";
  mine: boolean;
  owner: string | null;
  version: number;
}

interface Props {
  cfg: Cfg;
  set: (p: Cfg) => void;
  canEdit: boolean;
  builtins: [string, string][]; // [key, label]
  applyBuiltin: (key: string) => void;
}

/** "Quick style" menu: built-in styles, the user's saved styles, and save / manage actions. */
export default function StyleMenu({ cfg, set, canEdit, builtins, applyBuiltin }: Props) {
  const { lang } = useI18n();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [mine, setMine] = useState<FigureStyleItem[]>([]);
  const [saveOpen, setSaveOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);

  const load = () => api.get<{ mine: FigureStyleItem[] }>("/api/styles").then((r) => setMine(r.mine)).catch(() => {});
  useEffect(() => { load(); }, []);

  return (
    <>
      <select className="small" value="" aria-label="style" style={{ fontSize: 12, padding: "3px 6px" }}
        onChange={(e) => {
          const v = e.target.value;
          if (v.startsWith("b:")) applyBuiltin(v.slice(2));
          else if (v.startsWith("s:")) { const st = mine.find((x) => x.id === v.slice(2)); if (st) set(st.settings); }
          else if (v === "__save") setSaveOpen(true);
          else if (v === "__manage") setManageOpen(true);
        }}>
        <option value="">{T("Style rapide…", "Quick style…")}</option>
        <optgroup label={T("Styles intégrés", "Built-in styles")}>
          {builtins.map(([k, l]) => <option key={k} value={`b:${k}`}>{l}</option>)}
        </optgroup>
        {mine.length > 0 && <optgroup label={T("Mes styles", "My styles")}>
          {mine.map((s) => <option key={s.id} value={`s:${s.id}`}>★ {s.name}</option>)}
        </optgroup>}
        <optgroup label="—">
          <option value="__save">💾 {T("Enregistrer le style de cette figure…", "Save this figure’s style…")}</option>
          <option value="__manage">⚙ {T("Gérer mes styles…", "Manage my styles…")}</option>
        </optgroup>
      </select>
      {saveOpen && <SaveStyle cfg={cfg} onClose={() => setSaveOpen(false)} onSaved={() => { setSaveOpen(false); load(); }} />}
      {manageOpen && <ManageStyles mine={mine} canEdit={canEdit} reload={load} apply={(s) => { set(s.settings); setManageOpen(false); }} onClose={() => setManageOpen(false)} />}
    </>
  );
}

function SaveStyle({ cfg, onClose, onSaved }: { cfg: Cfg; onClose: () => void; onSaved: () => void }) {
  const { lang } = useI18n();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  async function save() {
    if (!name.trim()) return;
    try {
      await api.post("/api/styles", { name, from_config: cfg });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? (e.detail?.message?.[lang] ?? JSON.stringify(e.detail)) : String(e));
    }
  }
  return (
    <Modal title={T("Enregistrer le style", "Save the style")} onClose={onClose} narrow
      footer={<><button onClick={onClose}>{T("Annuler", "Cancel")}</button><button className="primary" disabled={!name.trim()} onClick={save}>{T("Enregistrer", "Save")}</button></>}>
      <label className="field"><span>{T("Nom du style", "Style name")}</span>
        <input autoFocus value={name} placeholder={T("ex. Style de Gab", "e.g. Gab’s style")} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && save()} /></label>
      <p className="small-text muted">{T(
        "Le style garde l’apparence : polices, tailles, traits, graduations, cadre, couleurs, symboles, affichage des étoiles et des p, légende. Il ne contient ni données, ni colonnes, ni bornes d’axes, ni titres : vous pouvez l’appliquer à n’importe quelle figure.",
        "The style keeps the look: fonts, sizes, lines, ticks, frame, colours, symbols, star and p display, legend. It holds no data, columns, axis ranges or titles, so it can be applied to any figure.")}</p>
      {err && <div className="notice error">{err}</div>}
    </Modal>
  );
}

function ManageStyles({ mine, canEdit, reload, apply, onClose }: { mine: FigureStyleItem[]; canEdit: boolean; reload: () => void; apply: (s: FigureStyleItem) => void; onClose: () => void }) {
  const { lang } = useI18n();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [err, setErr] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const file = useRef<HTMLInputElement>(null);
  const fail = (e: unknown) => setErr(e instanceof ApiError ? (e.detail?.message?.[lang] ?? JSON.stringify(e.detail)) : String(e));

  async function rename(s: FigureStyleItem) {
    const n = (names[s.id] ?? s.name).trim();
    if (!n || n === s.name) return;
    try { await api.put(`/api/styles/${s.id}`, { name: n, version: s.version }); reload(); } catch (e) { fail(e); }
  }
  async function del(s: FigureStyleItem) {
    if (!window.confirm(T(`Supprimer le style « ${s.name} » ?`, `Delete the style “${s.name}”?`))) return;
    try { await api.del(`/api/styles/${s.id}`); reload(); } catch (e) { fail(e); }
  }
  async function importFile(f: File) {
    try {
      const json = JSON.parse(await f.text());
      await api.post("/api/styles/import", json);
      reload();
      setErr("");
    } catch (e) { fail(e instanceof SyntaxError ? T("Fichier illisible.", "Unreadable file.") : e); }
  }
  return (
    <Modal title={T("Mes styles de figure", "My figure styles")} onClose={onClose}
      footer={<>
        <input ref={file} type="file" accept=".json,application/json" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && importFile(e.target.files[0])} />
        <button onClick={() => file.current?.click()}>⤒ {T("Importer un style (.fiole-style.json)", "Import a style (.fiole-style.json)")}</button>
        <button className="primary" onClick={onClose}>{T("Fermer", "Close")}</button></>}>
      {mine.length === 0 && <div className="empty">{T("Aucun style enregistré. Ouvrez une figure qui vous plaît, puis « Style rapide » → « Enregistrer le style de cette figure… ».", "No saved style yet. Open a figure you like, then “Quick style” → “Save this figure’s style…”.")}</div>}
      {mine.map((s) => (
        <div key={s.id} className="style-row">
          <input className="grow" value={names[s.id] ?? s.name} aria-label={T("Nom", "Name")} onChange={(e) => setNames({ ...names, [s.id]: e.target.value })}
            onBlur={() => rename(s)} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
          <span className="style-chips" aria-hidden>{Object.entries(s.settings).slice(0, 4).map(([k, v]) => <span key={k} className="badge">{k.replace(/_pt$/, "").replace(/_/g, " ")}: {Array.isArray(v) ? "…" : String(v)}</span>)}</span>
          <button className="small primary" disabled={!canEdit} onClick={() => apply(s)}>{T("Appliquer", "Apply")}</button>
          <a className="btn small" href={`/api/styles/${s.id}/export`}>{T("Exporter", "Export")}</a>
          <button className="small" disabled title={T("Bientôt disponible", "Coming soon")}>{T("Partager", "Share")} <span className="badge soon">{T("Bientôt", "Soon")}</span></button>
          <button className="small danger" onClick={() => del(s)}>{T("Supprimer", "Delete")}</button>
        </div>
      ))}
      <div className="notice info" style={{ marginTop: 12 }}>
        {T("Bientôt disponible : partager vos styles avec d’autres utilisateurs, parcourir les styles publics et les copier dans les vôtres. En attendant, exportez un style en fichier et envoyez-le.",
          "Coming soon: share your styles with other users, browse public styles and copy them into yours. Meanwhile, export a style as a file and send it.")}
      </div>
      {err && <div className="notice error">{err}</div>}
    </Modal>
  );
}
