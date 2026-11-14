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
  shared_with?: { user_id: string; email: string; display_name: string }[];
}
interface Lists { mine: FigureStyleItem[]; shared: FigureStyleItem[]; public: FigureStyleItem[] }

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
  const [lists, setLists] = useState<Lists>({ mine: [], shared: [], public: [] });
  const mine = lists.mine;
  const all = [...lists.mine, ...lists.shared, ...lists.public];
  const [saveOpen, setSaveOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);

  const load = () => api.get<Lists>("/api/styles").then(setLists).catch(() => {});
  useEffect(() => { load(); }, []);

  return (
    <>
      <select className="small" value="" aria-label="style" style={{ fontSize: 12, padding: "3px 6px" }}
        onChange={(e) => {
          const v = e.target.value;
          if (v.startsWith("b:")) applyBuiltin(v.slice(2));
          else if (v.startsWith("s:")) { const st = all.find((x) => x.id === v.slice(2)); if (st) set(st.settings); }
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
        {lists.shared.length > 0 && <optgroup label={T("Partagés avec moi", "Shared with me")}>
          {lists.shared.map((s) => <option key={s.id} value={`s:${s.id}`}>👥 {s.name} — {s.owner}</option>)}
        </optgroup>}
        {lists.public.length > 0 && <optgroup label={T("Galerie publique", "Public gallery")}>
          {lists.public.slice(0, 30).map((s) => <option key={s.id} value={`s:${s.id}`}>🌐 {s.name} — {s.owner}</option>)}
        </optgroup>}
        <optgroup label="—">
          <option value="__save">💾 {T("Enregistrer le style de cette figure…", "Save this figure’s style…")}</option>
          <option value="__manage">⚙ {T("Gérer et partager mes styles…", "Manage and share my styles…")}</option>
        </optgroup>
      </select>
      {saveOpen && <SaveStyle cfg={cfg} onClose={() => setSaveOpen(false)} onSaved={() => { setSaveOpen(false); load(); }} />}
      {manageOpen && <ManageStyles lists={lists} canEdit={canEdit} reload={load} apply={(s) => { set(s.settings); setManageOpen(false); }} onClose={() => setManageOpen(false)} />}
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

function ManageStyles({ lists, canEdit, reload, apply, onClose }: { lists: Lists; canEdit: boolean; reload: () => void; apply: (s: FigureStyleItem) => void; onClose: () => void }) {
  const { lang } = useI18n();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [err, setErr] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const file = useRef<HTMLInputElement>(null);
  const mine = lists.mine;
  const [tab, setTab] = useState<"mine" | "others">("mine");
  const [shareFor, setShareFor] = useState<string | null>(null);
  const [info, setInfo] = useState("");
  async function copy(s: FigureStyleItem) {
    try { await api.post(`/api/styles/${s.id}/copy`); reload(); setInfo(T(`« ${s.name} » copié dans vos styles.`, `“${s.name}” copied into your styles.`)); setTab("mine"); } catch (e) { fail(e); }
  }
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
    <Modal title={T("Styles de figure", "Figure styles")} onClose={onClose}
      footer={<>
        <input ref={file} type="file" accept=".json,application/json" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && importFile(e.target.files[0])} />
        <button onClick={() => file.current?.click()}>⤒ {T("Importer un style (.fiole-style.json)", "Import a style (.fiole-style.json)")}</button>
        <button className="primary" onClick={onClose}>{T("Fermer", "Close")}</button></>}>
      <div className="tabs" role="tablist" style={{ marginTop: -6 }}>
        <button role="tab" aria-selected={tab === "mine"} className={tab === "mine" ? "active" : ""} onClick={() => setTab("mine")}>{T("Mes styles", "My styles")} ({mine.length})</button>
        <button role="tab" aria-selected={tab === "others"} className={tab === "others" ? "active" : ""} onClick={() => setTab("others")}>{T("Partagés avec moi et galerie", "Shared with me & gallery")} ({lists.shared.length + lists.public.length})</button>
      </div>
      {info && <div className="notice info">{info}</div>}
      {tab === "others" && <>
        {lists.shared.length + lists.public.length === 0 && <div className="empty">{T("Personne n’a encore partagé de style avec vous, et la galerie publique est vide.", "Nobody has shared a style with you yet, and the public gallery is empty.")}</div>}
        {[...lists.shared.map((s) => [s, "👥"] as const), ...lists.public.map((s) => [s, "🌐"] as const)].map(([s, icon]) => (
          <div key={s.id} className="style-row">
            <span className="grow"><b>{icon} {s.name}</b> <span className="muted small-text">{T("par", "by")} {s.owner}</span></span>
            <span className="style-chips" aria-hidden>{Object.entries(s.settings).slice(0, 3).map(([k, v]) => <span key={k} className="badge">{k.replace(/_pt$/, "").replace(/_/g, " ")}: {Array.isArray(v) || typeof v === "object" ? "…" : String(v)}</span>)}</span>
            <button className="small primary" disabled={!canEdit} onClick={() => apply(s)}>{T("Appliquer", "Apply")}</button>
            <button className="small" onClick={() => copy(s)}>{T("Copier dans mes styles", "Copy into my styles")}</button>
          </div>))}
      </>}
      {tab === "mine" && mine.length === 0 && <div className="empty">{T("Aucun style enregistré. Ouvrez une figure qui vous plaît, puis « Style rapide » → « Enregistrer le style de cette figure… ».", "No saved style yet. Open a figure you like, then “Quick style” → “Save this figure’s style…”.")}</div>}
      {tab === "mine" && mine.map((s) => (
        <div key={s.id}>
        <div className="style-row">
          <input className="grow" value={names[s.id] ?? s.name} aria-label={T("Nom", "Name")} onChange={(e) => setNames({ ...names, [s.id]: e.target.value })}
            onBlur={() => rename(s)} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
          <span className="style-chips" aria-hidden>{Object.entries(s.settings).slice(0, 4).map(([k, v]) => <span key={k} className="badge">{k.replace(/_pt$/, "").replace(/_/g, " ")}: {Array.isArray(v) ? "…" : String(v)}</span>)}</span>
          <button className="small primary" disabled={!canEdit} onClick={() => apply(s)}>{T("Appliquer", "Apply")}</button>
          <a className="btn small" href={`/api/styles/${s.id}/export`}>{T("Exporter", "Export")}</a>
          <button className={`small ${shareFor === s.id ? "primary" : ""}`} onClick={() => setShareFor(shareFor === s.id ? null : s.id)}>
            {s.visibility === "public" ? "🌐" : s.visibility === "shared" ? "👥" : "🔒"} {T("Partager", "Share")}</button>
          <button className="small danger" onClick={() => del(s)}>{T("Supprimer", "Delete")}</button>
        </div>
        {shareFor === s.id && <StyleShare s={s} reload={reload} fail={fail} />}
        </div>
      ))}
      {err && <div className="notice error">{err}</div>}
    </Modal>
  );
}

function StyleShare({ s, reload, fail }: { s: FigureStyleItem; reload: () => void; fail: (e: unknown) => void }) {
  const { lang } = useI18n();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [emails, setEmails] = useState("");
  const [msg, setMsg] = useState("");
  const setVis = async (v: string) => { try { await api.put(`/api/styles/${s.id}`, { visibility: v, version: s.version }); reload(); } catch (e) { fail(e); } };
  async function add() {
    const list = emails.split(/[\s,;]+/).filter(Boolean);
    if (!list.length) return;
    try {
      const r = await api.post<{ unknown: string[]; email: Record<string, string> }>(`/api/styles/${s.id}/shares`, { emails: list });
      setEmails("");
      setMsg(r.unknown.length ? T(`Pas de compte Fiole pour : ${r.unknown.join(", ")}. Demandez-leur de s’inscrire, ou envoyez le fichier exporté.`,
        `No Fiole account for: ${r.unknown.join(", ")}. Ask them to sign up, or send the exported file.`) : T("Partagé.", "Shared."));
      reload();
    } catch (e) { fail(e); }
  }
  return (
    <div className="style-share">
      <div className="seg" role="radiogroup" aria-label={T("Visibilité", "Visibility")}>
        {([["private", T("🔒 Privé", "🔒 Private")], ["shared", T("👥 Personnes choisies", "👥 Chosen people")], ["public", T("🌐 Public (galerie)", "🌐 Public (gallery)")]] as const).map(([v, l]) => (
          <label key={v} className={s.visibility === v ? "on" : ""}><input type="radio" name={`vis-${s.id}`} checked={s.visibility === v} onChange={() => setVis(v)} /> {l}</label>))}
      </div>
      <p className="small-text muted" style={{ margin: "6px 0" }}>{s.visibility === "public"
        ? T("Tous les utilisateurs de Fiole voient ce style dans la galerie et peuvent l’appliquer ou le copier. Ils ne peuvent pas modifier le vôtre.", "Every Fiole user sees this style in the gallery and can apply or copy it. They cannot change yours.")
        : s.visibility === "shared" ? T("Seules les personnes ci-dessous le voient (« Partagés avec moi »). Elles peuvent l’appliquer ou le copier, pas modifier le vôtre.", "Only the people below see it (“Shared with me”). They can apply or copy it, not change yours.")
        : T("Vous seul le voyez.", "Only you can see it.")}</p>
      <div className="row">
        <input className="grow" placeholder={T("Partager avec (emails)", "Share with (emails)")} value={emails} onChange={(e) => setEmails(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="small primary" onClick={add} disabled={!emails.trim()}>{T("Partager", "Share")}</button>
      </div>
      {msg && <div className="small-text" style={{ marginTop: 4 }}>{msg}</div>}
      {(s.shared_with ?? []).map((u) => (
        <div key={u.user_id} className="row" style={{ marginTop: 4 }}>
          <span className="grow small-text">👤 {u.display_name} <span className="muted">{u.email}</span></span>
          <button className="ghost small" onClick={async () => { try { await api.del(`/api/styles/${s.id}/shares/${u.user_id}`); reload(); } catch (e) { fail(e); } }}>{T("Retirer", "Remove")}</button>
        </div>))}
    </div>
  );
}
