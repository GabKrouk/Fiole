import { useCallback, useEffect, useState } from "react";
import { Route, Routes, useNavigate, useParams, useLocation } from "react-router-dom";
import { api, ApiError, ProjectFull, AnalysisType } from "../api";
import { TopBar } from "../App";
import { useI18n } from "../i18n";
import DataView from "../components/DataView";
import AnalysisView from "../components/AnalysisView";
import FigureView from "../components/FigureView";
import ImportDialog from "../components/ImportDialog";
import Modal from "../components/Modal";

export interface ProjectCtx {
  project: ProjectFull;
  reload: () => Promise<void>;
  types: AnalysisType[];
}

function StatusDot({ status }: { status: string }) {
  const cls = status === "up_to_date" ? "ok" : status === "stale" ? "stale" : "never";
  return <span className={`dot ${cls}`} aria-hidden />;
}

export default function ProjectPage() {
  const { projectId } = useParams();
  const { t, L, lang } = useI18n();
  const nav = useNavigate();
  const loc = useLocation();
  const [project, setProject] = useState<ProjectFull | null>(null);
  const [types, setTypes] = useState<AnalysisType[]>([]);
  const [notFound, setNotFound] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [name, setName] = useState("");

  const reload = useCallback(async () => {
    try {
      const p = await api.get<ProjectFull>(`/api/projects/${projectId}`);
      setProject(p);
      setName(p.name);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNotFound(true);
    }
  }, [projectId]);

  useEffect(() => {
    reload();
    api.get<AnalysisType[]>("/api/analysis-types").then(setTypes);
  }, [reload]);

  if (notFound) return (<><TopBar /><div className="page"><div className="empty">404</div></div></>);
  if (!project) return <TopBar />;

  const base = `/p/${project.id}`;
  const active = loc.pathname;
  const ctx: ProjectCtx = { project, reload, types };
  const canEdit = project.permissions.includes("edit");

  async function rename() {
    if (!project || name.trim() === project.name || !name.trim()) return setName(project!.name);
    try {
      await api.patch(`/api/projects/${project.id}`, { name, version: project.version });
    } finally {
      reload();
    }
  }

  async function newEmptyTable() {
    const d = await api.post<{ id: string }>(`/api/projects/${project!.id}/datasets`, {
      name: `${t("table")} ${project!.datasets.length + 1}`,
      content: {
        columns: [{ id: "c1", name: "A", type: "numeric" }, { id: "c2", name: "B", type: "numeric" }],
        rows: Array.from({ length: 8 }, (_, i) => ({ id: `r${i + 1}`, cells: {} })),
        design: { layout: "wide", group_columns: ["c1", "c2"] },
        exclusions: [],
      },
    });
    await reload();
    nav(`${base}/data/${d.id}`);
  }

  async function newAnalysis() {
    const ds = project!.datasets[0];
    if (!ds) return;
    const a = await api.post<{ id: string }>(`/api/projects/${project!.id}/analyses`, {
      dataset_id: ds.id, kind: "ttest", name: `${t("analysisN")} ${project!.analyses.length + 1}`, config: { variant: "welch", alternative: "two-sided", ci_level: 0.95 },
    });
    await reload();
    nav(`${base}/analysis/${a.id}`);
  }

  async function newFigure() {
    const ds = project!.datasets[0];
    if (!ds) return;
    const f = await api.post<{ id: string }>(`/api/projects/${project!.id}/figures`, {
      dataset_id: ds.id, name: `Figure ${project!.figures.length + 1}`, config: { type: "scatter_groups", width_mm: 80, height_mm: 70 },
    });
    await reload();
    nav(`${base}/figure/${f.id}`);
  }

  const projectUrl = `${window.location.origin}${base}`;

  return (
    <>
      <TopBar right={<button className="primary" onClick={() => setShareOpen(true)}>{t("share")}</button>}>
        <input className="name-input" value={name} onChange={(e) => setName(e.target.value)} onBlur={rename}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} aria-label={t("projectName")} disabled={!canEdit} />
      </TopBar>
      <div className="workspace">
        <nav className="sidebar" aria-label="Project">
          <section>
            <h4>{t("data")}{canEdit && <span className="row tight">
              <button className="ghost small" title={t("emptyTable")} onClick={newEmptyTable}>+</button>
              <button className="ghost small" onClick={() => setImportOpen(true)}>{t("importData")}</button></span>}</h4>
            {project.datasets.length === 0 && <p className="small-text muted" style={{ padding: "0 8px" }}>{t("noData")}</p>}
            {project.datasets.map((d) => (
              <button key={d.id} className={`side-item ${active.includes(d.id) ? "active" : ""}`} onClick={() => nav(`${base}/data/${d.id}`)}>
                <span className="label">▦ {d.name}</span><span className="muted small-text">v{d.version}</span>
              </button>
            ))}
          </section>
          <section>
            <h4>{t("analyses")}{canEdit && project.datasets.length > 0 && <button className="ghost small" onClick={newAnalysis}>+</button>}</h4>
            {project.analyses.length === 0 && <p className="small-text muted" style={{ padding: "0 8px" }}>{t("nothingYet")}</p>}
            {project.analyses.map((a) => (
              <button key={a.id} className={`side-item ${active.includes(a.id) ? "active" : ""}`} onClick={() => nav(`${base}/analysis/${a.id}`)}
                title={a.status === "stale" ? t("staleExplain") : ""}>
                <StatusDot status={a.status} /><span className="label">{a.name}</span>
                {a.status === "stale" && <span className="badge stale">{t("stale")}</span>}
              </button>
            ))}
          </section>
          <section>
            <h4>{t("figures")}{canEdit && project.datasets.length > 0 && <button className="ghost small" onClick={newFigure}>+</button>}</h4>
            {project.figures.length === 0 && <p className="small-text muted" style={{ padding: "0 8px" }}>{t("nothingYet")}</p>}
            {project.figures.map((f) => (
              <button key={f.id} className={`side-item ${active.includes(f.id) ? "active" : ""}`} onClick={() => nav(`${base}/figure/${f.id}`)}>
                <span className="label">◩ {f.name}</span>{f.stale && <span className="badge stale">{t("stale")}</span>}
              </button>
            ))}
          </section>
          <section>
            <h4>{t("export")}</h4>
            <a className="side-item" href={`/api/projects/${project.id}/results.csv?lang=${lang}`}>{t("exportResults")}</a>
            <a className="side-item" href={`/api/projects/${project.id}/archive`}>{t("exportArchive")} (.fiole.json)</a>
            <button className="side-item" disabled>{t("reportSoon")} <span className="badge soon">{t("comingSoon")}</span></button>
          </section>
          <section>
            <h4>{t("unavailableTitle")}</h4>
            <div className="soon-list" style={{ padding: "0 6px" }}>
              {types.filter((x) => !x.available).map((x) => <span key={x.kind} className="badge soon" title={t("comingSoon")}>{L(x.label)}</span>)}
              <span className="badge soon">{t("multiPanel")}</span>
              <span className="badge soon">{t("transformations")}</span>
              <span className="badge soon">{t("duplicate")}</span>
              <span className="badge soon">{t("comments")}</span>
            </div>
          </section>
        </nav>
        <main className="main">
          <Routes>
            <Route path="data/:datasetId" element={<DataView ctx={ctx} />} />
            <Route path="analysis/:analysisId" element={<AnalysisView ctx={ctx} />} />
            <Route path="figure/:figureId" element={<FigureView ctx={ctx} />} />
            <Route path="*" element={
              <div className="empty" style={{ marginTop: 20 }}>
                <p>{project.datasets.length === 0 ? t("noData") : t("selectSomething")}</p>
                <p className="small-text">{t("steps")}</p>
                {project.datasets.length === 0 && canEdit && <div className="row" style={{ justifyContent: "center", marginTop: 12 }}>
                  <button className="primary" onClick={() => setImportOpen(true)}>{t("importData")}</button>
                  <button onClick={newEmptyTable}>{t("emptyTable")}</button></div>}
              </div>} />
          </Routes>
        </main>
      </div>
      {importOpen && <ImportDialog projectId={project.id} onClose={() => setImportOpen(false)} onCreated={async (id) => { setImportOpen(false); await reload(); nav(`${base}/data/${id}`); }} />}
      {shareOpen && (
        <Modal title={`${t("share")} « ${project.name} »`} onClose={() => setShareOpen(false)} narrow>
          <div className="notice info">{t("shareSoon")}</div>
          <div className="field"><span>{t("owner")}</span><div>{project.owner.display_name} ({project.owner.email})</div></div>
          <div className="row">
            <input className="grow mono" readOnly value={projectUrl} aria-label="URL" />
            <button onClick={() => { navigator.clipboard?.writeText(projectUrl); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? t("copied") : t("copyLink")}
            </button>
          </div>
          <p className="small-text muted" style={{ marginTop: 10 }}>
            {t("comingSoon")} : {["Ajouter des personnes / Add people", "Rôles / Roles", "Accès général / General access"].join(" · ")}
          </p>
        </Modal>
      )}
    </>
  );
}
