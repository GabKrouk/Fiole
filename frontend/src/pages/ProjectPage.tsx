import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Route, Routes, useNavigate, useParams, useLocation } from "react-router-dom";
import { api, ApiError, ProjectFull, AnalysisType, Dataset } from "../api";
import { smartDefaults } from "../columns";
import { TopBar } from "../App";
import { useI18n } from "../i18n";
import DataView from "../components/DataView";
import AnalysisView from "../components/AnalysisView";
import FigureView from "../components/FigureView";
import LayoutView from "../components/LayoutView";
import ImportDialog from "../components/ImportDialog";
import ShareDialog from "../components/ShareDialog";
import ProjectMenu from "../components/ProjectMenu";
import NoAccess from "../components/NoAccess";
import { BUILD } from "../theme";
import Modal from "../components/Modal";

interface Impact {
  analyses: { id: string; name: string }[];
  figures: { id: string; name: string }[];
  layouts: { id: string; name: string }[];
  figures_losing_stars: { id: string; name: string; count: number }[];
  layouts_losing_panels: { id: string; name: string; count: number }[];
}

function DeleteConfirm({ impact, lang, onClose, onGo }: { impact: Impact; lang: string; onClose: () => void; onGo: () => void }) {
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const n = impact.analyses.length + impact.figures.length + impact.layouts.length;
  const list = (title: string, items: { name: string }[]) => items.length > 0 && <><b>{title}</b><ul className="del-list">{items.map((x, i) => <li key={i}>{x.name}</li>)}</ul></>;
  return (
    <Modal title={T(`Supprimer ${n} élément${n > 1 ? "s" : ""} ?`, `Delete ${n} item${n > 1 ? "s" : ""}?`)} onClose={onClose} narrow
      footer={<><button onClick={onClose}>{T("Annuler", "Cancel")}</button><button className="danger primary" autoFocus onClick={onGo}>{T("Supprimer", "Delete")}</button></>}>
      {list(T("Analyses", "Analyses"), impact.analyses)}
      {list(T("Figures", "Figures"), impact.figures)}
      {list(T("Mises en page", "Layouts"), impact.layouts)}
      {(impact.figures_losing_stars.length > 0 || impact.layouts_losing_panels.length > 0) && <div className="notice warn">
        {impact.figures_losing_stars.map((f) => <div key={f.id}>⚠ {T(`« ${f.name} » perdra ses étoiles de significativité (elles viennent d’une analyse supprimée).`, `“${f.name}” will lose its significance stars (they come from a deleted analysis).`)}</div>)}
        {impact.layouts_losing_panels.map((l) => <div key={l.id}>⚠ {T(`La mise en page « ${l.name} » perdra ${l.count} panneau${l.count > 1 ? "x" : ""}.`, `Layout “${l.name}” will lose ${l.count} panel${l.count > 1 ? "s" : ""}.`)}</div>)}
      </div>}
      <p className="small-text muted">{T("Les données ne sont pas touchées. Vous pourrez annuler juste après.", "The data are not touched. You can undo right after.")}</p>
    </Modal>
  );
}

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
  const [shareOpen, setShareOpen] = useState(() => new URLSearchParams(window.location.search).has("share"));
  const [name, setName] = useState("");
  const [toast, setToastState] = useState<{ msg: string; undo?: () => void } | null>(null);
  const toastTimer = useRef<number>();
  const showToast = (msg: string, undo?: () => void, ms = 2200) => {
    window.clearTimeout(toastTimer.current);
    setToastState({ msg, undo });
    toastTimer.current = window.setTimeout(() => setToastState(null), ms);
  };
  const flash = (m: string) => showToast(m);
  const [sel, setSel] = useState<string[]>([]);
  const [confirm, setConfirm] = useState<{ keys: string[]; impact: Impact } | null>(null);

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

  // Cmd/Ctrl+C on an open figure, then Cmd/Ctrl+V: duplicate it (like copy-pasting a graph in Prism)
  useEffect(() => {
    const h = async (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable) return;
      if (!(e.metaKey || e.ctrlKey) || !project) return;
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) return; // the user is copying text
      const k = e.key.toLowerCase();
      const m = loc.pathname.match(/\/figure\/([^/]+)/);
      if (k === "c" && m) {
        try { sessionStorage.setItem("fiole-clip", JSON.stringify({ project: project.id, figure: m[1] })); } catch { /* ignore */ }
        flash(lang === "fr" ? "Figure copiée. Cmd/Ctrl+V pour la coller (dupliquer)." : "Figure copied. Cmd/Ctrl+V to paste (duplicate).");
      } else if (k === "v" && project.permissions.includes("edit")) {
        let clip: { project: string; figure: string } | null = null;
        try { clip = JSON.parse(sessionStorage.getItem("fiole-clip") || "null"); } catch { /* ignore */ }
        if (!clip || clip.project !== project.id) return;
        e.preventDefault();
        try {
          const made = await api.post<{ id: string }[]>(`/api/projects/${project.id}/figures/${clip.figure}/duplicate`, {});
          await reload();
          nav(`/p/${project.id}/figure/${made[0].id}`);
          flash(lang === "fr" ? "Figure collée : changez ses variables à gauche." : "Figure pasted: change its variables on the left.");
        } catch { flash(lang === "fr" ? "La figure copiée n’existe plus." : "The copied figure no longer exists."); }
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [project, loc.pathname, lang, reload, nav]); // eslint-disable-line react-hooks/exhaustive-deps

  if (notFound) return <NoAccess projectId={projectId!} />;
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

  function currentDataset() {
    return project!.datasets.find((d) => loc.pathname.includes(d.id)) ?? project!.datasets[0];
  }

  async function defaultsFor(id: string) {
    try {
      const d = await api.get<Dataset>(`/api/projects/${project!.id}/datasets/${id}`);
      return smartDefaults(d.content);
    } catch {
      return {};
    }
  }

  async function newAnalysis() {
    const ds = currentDataset();
    if (!ds) return;
    const smart = await defaultsFor(ds.id);
    const a = await api.post<{ id: string }>(`/api/projects/${project!.id}/analyses`, {
      dataset_id: ds.id, kind: "ttest", name: `${t("analysisN")} ${project!.analyses.length + 1}`, config: { variant: "welch", alternative: "two-sided", ci_level: 0.95, ...smart },
    });
    await reload();
    nav(`${base}/analysis/${a.id}`);
  }

  async function newFigure() {
    const ds = currentDataset();
    if (!ds) return;
    const smart = await defaultsFor(ds.id);
    const f = await api.post<{ id: string }>(`/api/projects/${project!.id}/figures`, {
      dataset_id: ds.id, name: `Figure ${project!.figures.length + 1}`, config: { type: "scatter_groups", width_mm: 80, height_mm: 70, ...smart },
    });
    await reload();
    nav(`${base}/figure/${f.id}`);
  }

  async function newLayout() {
    const figs = project!.figures.slice(-4).map((f) => f.id);
    const cols = figs.length <= 1 ? 1 : 2;
    const rows = figs.length > 2 ? 2 : 1;
    const l = await api.post<{ id: string }>(`/api/projects/${project!.id}/layouts`, {
      name: `${lang === "fr" ? "Mise en page" : "Layout"} ${project!.layouts.length + 1}`,
      config: { width_mm: cols === 1 ? 85 : 180, height_mm: rows === 1 ? (cols === 1 ? 75 : 80) : 150, rows, cols, panels: figs.map((f, i) => ({ cell: i, figure_id: f })) },
    });
    await reload();
    nav(`${base}/layout/${l.id}`);
  }

  const toggle = (k: string) => setSel((s) => (s.includes(k) ? s.filter((x) => x !== k) : [...s, k]));
  const Item = ({ k, to, title, children }: { k: string; to: string; title?: string; children: ReactNode }) => {
    const id = k.slice(2);
    const on = sel.includes(k);
    return (
      <div className={`side-row ${on ? "selected" : ""} ${sel.length ? "selecting" : ""}`}>
        {canEdit && sel.length > 0 && <input type="checkbox" className="side-check" checked={on} onChange={() => toggle(k)} aria-label={lang === "fr" ? "Sélectionner" : "Select"} />}
        <button className={`side-item ${active.includes(id) ? "active" : ""}`} title={title}
          onClick={(e) => { if (canEdit && (e.metaKey || e.ctrlKey || e.shiftKey)) { e.preventDefault(); toggle(k); } else if (sel.length) toggle(k); else nav(to); }}>
          {children}
        </button>
        {canEdit && !sel.length && <button className="side-del" title={lang === "fr" ? "Supprimer (Cmd/Maj-clic pour en sélectionner plusieurs)" : "Delete (Cmd/Shift-click to select several)"}
          aria-label={lang === "fr" ? "Supprimer" : "Delete"} onClick={() => askDelete([k])}>🗑</button>}
      </div>
    );
  };
  const bodyOf = (keys: string[]) => ({
    analyses: keys.filter((k) => k.startsWith("a:")).map((k) => k.slice(2)),
    figures: keys.filter((k) => k.startsWith("f:")).map((k) => k.slice(2)),
    layouts: keys.filter((k) => k.startsWith("l:")).map((k) => k.slice(2)),
  });
  async function askDelete(keys: string[]) {
    const r = await api.post<{ impact: Impact }>(`/api/projects/${project!.id}/bulk-delete`, { ...bodyOf(keys), dry_run: true });
    setConfirm({ keys, impact: r.impact });
  }
  async function doDelete(keys: string[]) {
    setConfirm(null);
    const r = await api.post<{ trash_id: number }>(`/api/projects/${project!.id}/bulk-delete`, bodyOf(keys));
    setSel([]);
    if (keys.some((k) => loc.pathname.includes(k.slice(2)))) nav(base);
    await reload();
    const n = keys.length;
    showToast(lang === "fr" ? `${n} élément${n > 1 ? "s" : ""} supprimé${n > 1 ? "s" : ""}.` : `${n} item${n > 1 ? "s" : ""} deleted.`, async () => {
      await api.post(`/api/projects/${project!.id}/trash/${r.trash_id}/restore`);
      await reload();
      showToast(lang === "fr" ? "Suppression annulée : tout est revenu." : "Deletion undone: everything is back.");
    }, 12000);
  }

  const myRole = project.role ?? "viewer";
  const roleName = (r: string) => ({ owner: t("owner"), editor: lang === "fr" ? "Éditeur" : "Editor", viewer: lang === "fr" ? "Lecteur" : "Viewer" } as Record<string, string>)[r] ?? r;

  return (
    <>
      <TopBar right={<>
        {myRole !== "owner" && <span className={`badge ${canEdit ? "ok" : ""}`} title={`${t("owner")} : ${project.owner.display_name}`}>{roleName(myRole)}{project.via_link ? (lang === "fr" ? " · via le lien" : " · via link") : ""}</span>}
        <button className="primary" onClick={() => setShareOpen(true)}>🔒 {t("share")}</button>
        <ProjectMenu inProject project={project} onChanged={reload} onShare={() => setShareOpen(true)} toast={(m, u) => showToast(m, u, u ? 8000 : 2200)} /></>}>
        <input className="name-input" value={name} onChange={(e) => setName(e.target.value)} onBlur={rename}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} aria-label={t("projectName")} disabled={!canEdit} />
        <nav className="nav-tabs" aria-label={t("projectName")}>
          {([["data", t("data"), project.datasets], ["analysis", t("analyses"), project.analyses], ["figure", t("figures"), project.figures]] as [string, string, { id: string }[]][]).map(([k, label, items]) => {
            const on = active.includes(`/${k}/`);
            const target = on ? active : items.length ? `${base}/${k}/${items[items.length - 1].id}` : base;
            return <a key={k} href={target} className={on ? "active" : ""} onClick={(e) => { e.preventDefault(); nav(target); }}>{label}<span className="count">{items.length}</span></a>;
          })}
        </nav>
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
              <Item key={a.id} k={`a:${a.id}`} to={`${base}/analysis/${a.id}`} title={a.status === "stale" ? t("staleExplain") : ""}>
                <StatusDot status={a.status} /><span className="label">{a.name}</span>
                {a.status === "stale" && <span className="badge stale">{t("stale")}</span>}
              </Item>
            ))}
          </section>
          <section>
            <h4>{t("figures")}{canEdit && project.datasets.length > 0 && <button className="ghost small" onClick={newFigure}>+</button>}</h4>
            {project.figures.length === 0 && <p className="small-text muted" style={{ padding: "0 8px" }}>{t("nothingYet")}</p>}
            {project.figures.map((f) => (
              <Item key={f.id} k={`f:${f.id}`} to={`${base}/figure/${f.id}`}>
                <span className="label">◩ {f.name}</span>{f.stale && <span className="badge stale">{t("stale")}</span>}
              </Item>
            ))}
          </section>
          <section>
            <h4>{lang === "fr" ? "Mises en page (panneaux A, B, C…)" : "Layouts (panels A, B, C…)"}{canEdit && project.figures.length > 0 && <button className="ghost small" title={lang === "fr" ? "Nouvelle mise en page" : "New layout"} onClick={newLayout}>+</button>}</h4>
            {project.layouts.length === 0 && <p className="small-text muted" style={{ padding: "0 8px" }}>{project.figures.length ? (lang === "fr" ? "Assemblez plusieurs figures sur une page." : "Put several figures on one page.") : t("nothingYet")}</p>}
            {project.layouts.map((l) => (
              <Item key={l.id} k={`l:${l.id}`} to={`${base}/layout/${l.id}`}>
                <span className="label">▤ {l.name}</span><span className="muted small-text">{(l.config.panels ?? []).length}</span>
              </Item>
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
              <span className="badge soon">{t("transformations")}</span>
              <span className="badge soon">{t("comments")}</span>
            </div>
          </section>
          {sel.length > 0 && <div className="sel-bar">
            <span className="grow">{sel.length} {lang === "fr" ? `sélectionné${sel.length > 1 ? "s" : ""}` : "selected"}</span>
            <button className="small danger" onClick={() => askDelete(sel)}>🗑 {lang === "fr" ? "Supprimer la sélection" : "Delete selection"}</button>
            <button className="ghost small" aria-label={lang === "fr" ? "Annuler la sélection" : "Clear selection"} onClick={() => setSel([])}>✕</button>
          </div>}
          <div className="build-stamp" title="build">fiole · {BUILD}</div>
        </nav>
        <main className="main">
          {!canEdit && <div className="notice info readonly-banner">👁 {lang === "fr"
            ? `Lecture seule : ${project.owner.display_name} vous a donné un accès lecteur. Vous pouvez consulter et exporter, pas modifier.`
            : `Read-only: ${project.owner.display_name} gave you viewer access. You can view and export, not edit.`}</div>}
          <Routes>
            <Route path="data/:datasetId" element={<DataView ctx={ctx} />} />
            <Route path="analysis/:analysisId" element={<AnalysisView ctx={ctx} />} />
            <Route path="figure/:figureId" element={<FigureView ctx={ctx} />} />
            <Route path="layout/:layoutId" element={<LayoutView key={loc.pathname} ctx={ctx} />} />
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
      {toast && <div className="toast" role="status">{toast.msg}
        {toast.undo && <button className="small" style={{ marginLeft: 10 }} onClick={() => { const u = toast.undo!; setToastState(null); u(); }}>↶ {lang === "fr" ? "Annuler" : "Undo"}</button>}</div>}
      {confirm && <DeleteConfirm impact={confirm.impact} lang={lang} onClose={() => setConfirm(null)} onGo={() => doDelete(confirm.keys)} />}
      {importOpen && <ImportDialog projectId={project.id} onClose={() => setImportOpen(false)} onCreated={async (id) => { setImportOpen(false); await reload(); nav(`${base}/data/${id}`); }} />}
      {shareOpen && <ShareDialog projectId={project.id} projectName={project.name} onClose={() => { setShareOpen(false); if (loc.search.includes("share")) nav(loc.pathname, { replace: true }); }} onLeft={() => nav("/")} />}
    </>
  );
}
