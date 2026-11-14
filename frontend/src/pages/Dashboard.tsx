import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ProjectSummary } from "../api";
import { TopBar } from "../App";
import { useI18n } from "../i18n";

type Scope = "mine" | "shared" | "recent";

export default function Dashboard() {
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const [scope, setScope] = useState<Scope>("mine");
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setProjects(null);
    api.get<ProjectSummary[]>(`/api/projects?scope=${scope}`).then(setProjects).catch(() => setProjects([]));
  }, [scope]);

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
          {(["mine", "shared", "recent"] as Scope[]).map((s) => (
            <button key={s} role="tab" aria-selected={scope === s} className={scope === s ? "active" : ""} onClick={() => setScope(s)}>
              {s === "mine" ? t("myProjects") : s === "shared" ? t("sharedWithMe") : t("recent")}
            </button>
          ))}
        </div>
        {projects === null ? null : projects.length === 0 ? (
          <div className="empty">{scope === "mine" ? t("noProjects") : scope === "shared" ? t("sharedEmpty") : t("recentEmpty")}</div>
        ) : (
          <div className="project-list">
            {projects.map((p) => (
              <Link key={p.id} to={p.url} className="project-card">
                <div className="name">{p.name}</div>
                <div className="small-text muted">{t("owner")} : {p.owner.display_name}</div>
                <div className="small-text muted">
                  {scope === "recent" && p.last_opened_at ? date(p.last_opened_at) : `${t("updated")} ${date(p.updated_at)}`}
                </div>
              </Link>
            ))}
          </div>
        )}
      </main>
    </>
  );
}
