import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ProjectSummary } from "../api";
import { TopBar } from "../App";
import { useI18n } from "../i18n";
import ProjectMenu from "../components/ProjectMenu";

type Scope = "mine" | "shared" | "recent" | "trash";
type Listed = ProjectSummary & { deleted_at?: string };

export default function Dashboard() {
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const [scope, setScope] = useState<Scope>("mine");
  const [projects, setProjects] = useState<Listed[] | null>(null);
  const [toast, setToast] = useState<{ msg: string; undo?: () => void } | null>(null);
  const toastTimer = useRef<number>();
  const showToast = (msg: string, undo?: () => void) => {
    window.clearTimeout(toastTimer.current);
    setToast({ msg, undo });
    toastTimer.current = window.setTimeout(() => setToast(null), undo ? 8000 : 2500);
  };
  const [tick, setTick] = useState(0);
  const refresh = () => setTick((n) => n + 1);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const lastScope = useRef(scope);
  useEffect(() => {
    if (lastScope.current !== scope) setProjects(null); // keep the list on screen while it refreshes
    lastScope.current = scope;
    api.get<Listed[]>(`/api/projects?scope=${scope}`).then(setProjects).catch(() => setProjects([]));
  }, [scope, tick]);
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  // a project just trashed from its own window: offer "Undo" here
  const loc = useLocation();
  useEffect(() => {
    const tr = (loc.state as { trashed?: { id: string; name: string } } | null)?.trashed;
    if (!tr) return;
    nav(".", { replace: true, state: null });
    showToast(T(`« ${tr.name} » est dans la corbeille.`, `“${tr.name}” moved to the trash.`), async () => {
      await api.post(`/api/projects/${tr.id}/restore`);
      nav(`/p/${tr.id}`);
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  async function restore(p: Listed) {
    await api.post(`/api/projects/${p.id}/restore`);
    showToast(T(`« ${p.name} » est restauré.`, `“${p.name}” restored.`));
    refresh();
  }
  async function purge(p: Listed) {
    if (!window.confirm(T(`Supprimer définitivement « ${p.name} » ? Données, analyses et figures seront effacées. C’est irréversible.`,
      `Delete “${p.name}” for good? Data, analyses and figures will be erased. This cannot be undone.`))) return;
    await api.del(`/api/projects/${p.id}/permanent`);
    showToast(T("Projet supprimé définitivement.", "Project deleted for good."));
    refresh();
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const p = await api.post<ProjectSummary>("/api/projects", { name });
    nav(p.url);
  }

  async function importArchive(f: File) {
    const fd = new FormData();
    fd.append("file", f);
    const r = await api.form<{ url: string }>("/api/projects/import-archive", fd);
    nav(r.url);
  }

  const date = (s: string) => new Date(s).toLocaleString(lang === "fr" ? "fr-FR" : "en-GB", { dateStyle: "medium", timeStyle: "short" });

  return (
    <>
      <TopBar />
      <main className="page">
        <div className="row" style={{ marginBottom: 6 }}>
          <h1 className="grow">{t("dashboard")}</h1>
          <button onClick={() => fileRef.current?.click()}>{t("importArchive")}</button>
          <input ref={fileRef} type="file" accept=".json,application/json" hidden onChange={(e) => e.target.files?.[0] && importArchive(e.target.files[0])} />
          <button className="primary" onClick={() => setCreating(true)}>+ {t("newProject")}</button>
        </div>
        <p className="muted" style={{ marginBottom: 18 }}>{t("steps")}</p>
        {creating && (
          <form className="panel row" onSubmit={create}>
            <input autoFocus className="grow" placeholder={t("projectName")} value={name} onChange={(e) => setName(e.target.value)} aria-label={t("projectName")} />
            <button className="primary" disabled={!name.trim()}>{t("create")}</button>
            <button type="button" onClick={() => setCreating(false)}>{t("cancel")}</button>
          </form>
        )}
        <div className="tabs" role="tablist">
          {(["mine", "shared", "recent", "trash"] as Scope[]).map((s) => (
            <button key={s} role="tab" aria-selected={scope === s} className={`${scope === s ? "active" : ""} ${s === "trash" ? "tab-trash" : ""}`} onClick={() => setScope(s)}>
              {s === "mine" ? t("myProjects") : s === "shared" ? t("sharedWithMe") : s === "recent" ? t("recent") : `🗑 ${T("Corbeille", "Trash")}`}
            </button>
          ))}
        </div>
        {projects === null ? null : projects.length === 0 ? (
          <div className="empty">{scope === "mine" ? t("noProjects") : scope === "shared" ? t("sharedEmpty") : scope === "recent" ? t("recentEmpty")
            : T("La corbeille est vide.", "The trash is empty.")}</div>
        ) : scope === "trash" ? (
          <>
            <p className="small-text muted" style={{ marginTop: 0 }}>{T("Les projets mis à la corbeille ne sont plus visibles par personne. Restaurez-les à tout moment.", "Projects in the trash are hidden from everyone. Restore them at any time.")}</p>
            <div className="project-list">
              {projects.map((p) => (
                <div key={p.id} className="project-card trashed">
                  <div className="name">{p.name}</div>
                  <div className="small-text muted">{T("Mis à la corbeille", "Trashed")} {p.deleted_at ? date(p.deleted_at) : ""}</div>
                  <div className="row tight" style={{ marginTop: 10 }}>
                    <button className="small primary" onClick={() => restore(p)}>↺ {T("Restaurer", "Restore")}</button>
                    <button className="small danger" onClick={() => purge(p)}>{T("Supprimer définitivement", "Delete for good")}</button>
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="project-list">
            {projects.map((p) => (
              <Link key={p.id} to={p.url} className="project-card">
                <ProjectMenu project={p} onChanged={refresh} toast={showToast} />
                <div className="name">{p.name}</div>
                <div className="small-text muted">{t("owner")} : {p.owner.display_name}
                  {p.role && p.role !== "owner" && <> · <span className="badge">{p.role === "editor" ? (lang === "fr" ? "Éditeur" : "Editor") : (lang === "fr" ? "Lecteur" : "Viewer")}{p.via_link ? (lang === "fr" ? " · lien" : " · link") : ""}</span></>}</div>
                <div className="small-text muted">
                  {scope === "recent" && p.last_opened_at ? date(p.last_opened_at) : `${t("updated")} ${date(p.updated_at)}`}
                </div>
                {p.role === "owner" && <button className="small card-delete" onClick={async (e) => {
                  e.preventDefault(); e.stopPropagation();
                  await api.del(`/api/projects/${p.id}`);
                  refresh();
                  showToast(T(`« ${p.name} » est dans la corbeille.`, `“${p.name}” moved to the trash.`), async () => { await api.post(`/api/projects/${p.id}/restore`); refresh(); });
                }}>🗑 {T("Supprimer", "Delete")}</button>}
              </Link>
            ))}
          </div>
        )}
      </main>
      {toast && <div className="toast" role="status">{toast.msg}{toast.undo && <> <button className="small" onClick={async () => { const u = toast.undo!; setToast(null); await u(); }}>{T("Annuler", "Undo")}</button></>}</div>}
    </>
  );
}
