import { DragEvent, FormEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, ProjectSummary } from "../api";
import { TopBar } from "../App";
import { useI18n } from "../i18n";
import ProjectMenu from "../components/ProjectMenu";
import MoveDialog, { FolderT } from "../components/MoveDialog";
import { FIO_ACCEPT, openFio } from "../lib/fio";

type Scope = "mine" | "shared" | "recent" | "trash";
type Listed = ProjectSummary & { deleted_at?: string };
const DND_PROJECT = "application/x-fiole-project";
const DND_FOLDER = "application/x-fiole-folder";

export default function Dashboard() {
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [scope, setScope] = useState<Scope>("mine");
  const [projects, setProjects] = useState<Listed[] | null>(null);
  const [folders, setFolders] = useState<FolderT[]>([]);
  const [toast, setToast] = useState<{ msg: string; undo?: () => void } | null>(null);
  const toastTimer = useRef<number>();
  const showToast = (msg: string, undo?: () => void) => {
    window.clearTimeout(toastTimer.current);
    setToast({ msg, undo });
    toastTimer.current = window.setTimeout(() => setToast(null), undo ? 8000 : 2500);
  };
  const [tick, setTick] = useState(0);
  const refresh = () => setTick((n) => n + 1);
  const [creating, setCreating] = useState<null | "project" | "folder">(null);
  const [name, setName] = useState("");
  const [opening, setOpening] = useState(false);
  const [fileOver, setFileOver] = useState(false);
  const [dropOn, setDropOn] = useState<string | null>(null); // folder id ("" = top level) under a dragged card
  const [movingFolder, setMovingFolder] = useState<FolderT | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const byId = new Map(folders.map((f) => [f.id, f]));
  const folderParam = params.get("dossier");
  const here: string | null = folderParam && byId.has(folderParam) ? folderParam : null;
  const goFolder = (id: string | null) => { setScope("mine"); setParams(id ? { dossier: id } : {}); };
  const path: FolderT[] = [];
  for (let f = here ? byId.get(here) : undefined; f; f = f.parent_id ? byId.get(f.parent_id) : undefined) path.unshift(f);

  const lastScope = useRef(scope);
  useEffect(() => {
    if (lastScope.current !== scope) setProjects(null); // keep the list on screen while it refreshes
    lastScope.current = scope;
    api.get<Listed[]>(`/api/projects?scope=${scope}`).then(setProjects).catch(() => setProjects([]));
    api.get<FolderT[]>("/api/folders").then(setFolders).catch(() => setFolders([]));
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
    if (creating === "folder") {
      await api.post<FolderT>("/api/folders", { name: name.trim(), parent_id: here });
      setName("");
      setCreating(null);
      refresh();
      return;
    }
    const p = await api.post<ProjectSummary>("/api/projects", { name, folder_id: scope === "mine" ? here : null });
    nav(p.url);
  }

  async function open(f: File) {
    if (opening) return;
    setOpening(true);
    showToast(T(`Ouverture de « ${f.name} »…`, `Opening “${f.name}”…`));
    try {
      const r = await openFio(f, scope === "mine" ? here : null);
      nav(r.url);
    } catch (e) {
      const m = e instanceof ApiError && e.detail?.message ? e.detail.message[lang] : T("Ce fichier ne peut pas être ouvert.", "This file cannot be opened.");
      showToast(m);
    } finally {
      setOpening(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const fail = (e: unknown) => showToast(e instanceof ApiError && e.detail?.message ? e.detail.message[lang] : T("Action impossible.", "Could not do that."));
  async function moveProject(id: string, folderId: string | null) {
    await api.put(`/api/projects/${id}/folder`, { folder_id: folderId });
    refresh();
  }
  async function moveFolder(id: string, parentId: string | null) {
    if (id === parentId) return;
    await api.patch(`/api/folders/${id}`, { parent_id: parentId });
    refresh();
  }
  async function renameFolder(f: FolderT) {
    const n = window.prompt(T("Nouveau nom du dossier", "New folder name"), f.name);
    if (!n || !n.trim() || n.trim() === f.name) return;
    await api.patch(`/api/folders/${f.id}`, { name: n.trim() });
    refresh();
  }
  async function deleteFolder(f: FolderT) {
    const up = f.parent_id ? `« ${byId.get(f.parent_id)?.name ?? ""} »` : T("« Mes projets »", "“My projects”");
    const inside = f.n_projects + f.n_folders;
    if (inside && !window.confirm(T(`Supprimer le dossier « ${f.name} » ? Les projets et dossiers qu’il contient ne sont pas supprimés : ils vont dans ${up}.`,
      `Delete the folder “${f.name}”? The projects and folders inside are not deleted: they move to ${up}.`))) return;
    await api.del(`/api/folders/${f.id}`);
    if (here === f.id) goFolder(f.parent_id);
    showToast(T(`Dossier « ${f.name} » supprimé.`, `Folder “${f.name}” deleted.`));
    refresh();
  }

  // drag a project or a folder onto a folder (or onto the path above) to move it there
  const dragTarget = (target: string | null) => ({
    onDragOver: (e: DragEvent) => {
      const ty = e.dataTransfer.types;
      if (!ty.includes(DND_PROJECT) && !ty.includes(DND_FOLDER)) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = "move";
      setDropOn(target ?? "");
    },
    onDragLeave: () => setDropOn((d) => (d === (target ?? "") ? null : d)),
    onDrop: (e: DragEvent) => {
      const pid = e.dataTransfer.getData(DND_PROJECT);
      const fid = e.dataTransfer.getData(DND_FOLDER);
      if (!pid && !fid) return;
      e.preventDefault();
      e.stopPropagation();
      setDropOn(null);
      (pid ? moveProject(pid, target) : moveFolder(fid, target)).catch(fail);
    },
  });
  // drop a .fio file anywhere on the page to open it
  const pageDrop = {
    onDragOver: (e: DragEvent) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setFileOver(true); } },
    onDragLeave: (e: DragEvent) => { if (e.currentTarget === e.target || !e.currentTarget.contains(e.relatedTarget as Node)) setFileOver(false); },
    onDrop: (e: DragEvent) => {
      if (!e.dataTransfer.files?.length) return;
      e.preventDefault();
      setFileOver(false);
      open(e.dataTransfer.files[0]);
    },
  };

  const date = (s: string) => new Date(s).toLocaleString(lang === "fr" ? "fr-FR" : "en-GB", { dateStyle: "medium", timeStyle: "short" });
  const subFolders = folders.filter((f) => (f.parent_id && byId.has(f.parent_id) ? f.parent_id : null) === here).sort((a, b) => a.name.localeCompare(b.name));
  const shown = projects === null ? null : scope === "mine" ? projects.filter((p) => (p.folder_id && byId.has(p.folder_id) ? p.folder_id : null) === here) : projects;
  const count = (f: FolderT) => f.n_projects + f.n_folders === 0 ? T("Vide", "Empty") : [
    f.n_folders ? `${f.n_folders} ${T(f.n_folders > 1 ? "dossiers" : "dossier", f.n_folders > 1 ? "folders" : "folder")}` : "",
    f.n_projects ? `${f.n_projects} ${T(f.n_projects > 1 ? "projets" : "projet", f.n_projects === 1 ? "project" : "projects")}` : "",
  ].filter(Boolean).join(" · ");

  return (
    <>
      <TopBar />
      <main className={`page ${fileOver ? "file-over" : ""}`} {...pageDrop}>
        <div className="row" style={{ marginBottom: 6 }}>
          <h1 className="grow">{t("dashboard")}</h1>
          <button onClick={() => fileRef.current?.click()} disabled={opening} title={T("Ouvre un projet enregistré dans un fichier .fio (le vôtre ou celui qu’on vous a envoyé). Vous pouvez aussi glisser le fichier sur cette page.", "Opens a project saved in a .fio file (yours or one sent to you). You can also drop the file on this page.")}>📂 {t("importArchive")}</button>
          <input ref={fileRef} type="file" accept={FIO_ACCEPT} hidden onChange={(e) => e.target.files?.[0] && open(e.target.files[0])} />
          {scope === "mine" && <button onClick={() => { setCreating("folder"); setName(""); }}>📁 {T("Nouveau dossier", "New folder")}</button>}
          <button className="primary" onClick={() => { setCreating("project"); setName(""); }}>+ {t("newProject")}</button>
        </div>
        <p className="muted" style={{ marginBottom: 18 }}>{t("steps")}</p>
        {creating && (
          <form className="panel row" onSubmit={create}>
            <input autoFocus className="grow" placeholder={creating === "folder" ? T("Nom du dossier", "Folder name") : t("projectName")} value={name}
              onChange={(e) => setName(e.target.value)} aria-label={creating === "folder" ? T("Nom du dossier", "Folder name") : t("projectName")} />
            <button className="primary" disabled={!name.trim()}>{t("create")}</button>
            <button type="button" onClick={() => setCreating(null)}>{t("cancel")}</button>
          </form>
        )}
        <div className="tabs" role="tablist">
          {(["mine", "shared", "recent", "trash"] as Scope[]).map((s) => (
            <button key={s} role="tab" aria-selected={scope === s} className={`${scope === s ? "active" : ""} ${s === "trash" ? "tab-trash" : ""}`}
              onClick={() => { setScope(s); if (s !== "mine" && here) setParams({}); }}>
              {s === "mine" ? t("myProjects") : s === "shared" ? t("sharedWithMe") : s === "recent" ? t("recent") : `🗑 ${T("Corbeille", "Trash")}`}
            </button>
          ))}
        </div>
        {scope === "mine" && path.length > 0 && (
          <nav className="crumbs" aria-label={T("Dossiers", "Folders")}>
            <button className={`crumb ${dropOn === "" ? "drop" : ""}`} onClick={() => goFolder(null)} {...dragTarget(null)}>🏠 {t("myProjects")}</button>
            {path.map((f) => (
              <span key={f.id} className="crumb-wrap">
                <span className="crumb-sep">›</span>
                <button className={`crumb ${dropOn === f.id ? "drop" : ""} ${f.id === here ? "current" : ""}`} onClick={() => goFolder(f.id)} {...dragTarget(f.id)}>📁 {f.name}</button>
              </span>
            ))}
          </nav>
        )}
        {shown === null ? null : scope === "trash" ? (
          shown.length === 0 ? <div className="empty">{T("La corbeille est vide.", "The trash is empty.")}</div> : <>
            <p className="small-text muted" style={{ marginTop: 0 }}>{T("Les projets mis à la corbeille ne sont plus visibles par personne. Restaurez-les à tout moment.", "Projects in the trash are hidden from everyone. Restore them at any time.")}</p>
            <div className="project-list">
              {shown.map((p) => (
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
        ) : shown.length === 0 && (scope !== "mine" || subFolders.length === 0) ? (
          <div className="empty">{scope === "mine" ? (here ? T("Ce dossier est vide. Glissez-y des projets, ou créez-en un ici avec « + Nouveau projet ».", "This folder is empty. Drag projects into it, or create one here with “+ New project”.") : t("noProjects"))
            : scope === "shared" ? t("sharedEmpty") : t("recentEmpty")}</div>
        ) : (
          <div className="project-list">
            {scope === "mine" && subFolders.map((f) => (
              <div key={f.id} className={`folder-card ${dropOn === f.id ? "drop" : ""}`} role="button" tabIndex={0}
                draggable onDragStart={(e) => { e.dataTransfer.setData(DND_FOLDER, f.id); e.dataTransfer.effectAllowed = "move"; }}
                onClick={() => goFolder(f.id)} onKeyDown={(e) => e.key === "Enter" && goFolder(f.id)} {...dragTarget(f.id)}>
                <FolderMenu onRename={() => renameFolder(f).catch(fail)} onMove={() => setMovingFolder(f)} onDelete={() => deleteFolder(f).catch(fail)} T={T} />
                <div className="name"><span className="folder-ico" aria-hidden>📁</span>{f.name}</div>
                <div className="small-text muted">{count(f)}</div>
              </div>
            ))}
            {shown.map((p) => (
              <Link key={p.id} to={p.url} className="project-card" draggable={p.via_link ? false : true}
                onDragStart={(e) => { e.dataTransfer.setData(DND_PROJECT, p.id); e.dataTransfer.effectAllowed = "move"; }}>
                <ProjectMenu project={p} onChanged={refresh} toast={showToast} />
                <div className="name">{p.name}</div>
                <div className="small-text muted">{t("owner")} : {p.owner.display_name}
                  {p.role && p.role !== "owner" && <> · <span className="badge">{p.role === "editor" ? (lang === "fr" ? "Éditeur" : "Editor") : (lang === "fr" ? "Lecteur" : "Viewer")}{p.via_link ? (lang === "fr" ? " · lien" : " · link") : ""}</span></>}</div>
                <div className="small-text muted">
                  {scope === "recent" && p.last_opened_at ? date(p.last_opened_at) : `${t("updated")} ${date(p.updated_at)}`}
                </div>
                {scope !== "mine" && p.folder_id && byId.has(p.folder_id) && <div className="small-text muted">📁 {byId.get(p.folder_id)!.name}</div>}
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
        {scope === "mine" && shown && shown.length + subFolders.length > 0 && folders.length > 0 && (
          <p className="small-text muted" style={{ marginTop: 14 }}>{T("Astuce : glissez un projet sur un dossier pour l’y ranger. Vos dossiers ne sont visibles que par vous.", "Tip: drag a project onto a folder to file it. Only you see your folders.")}</p>
        )}
        {fileOver && <div className="file-drop-hint" aria-hidden>📂 {T("Déposez le fichier .fio pour ouvrir le projet", "Drop the .fio file to open the project")}</div>}
      </main>
      {movingFolder && createPortal(
        <MoveDialog what={movingFolder.name} current={movingFolder.parent_id} skip={movingFolder.id}
          onPick={(to) => moveFolder(movingFolder.id, to).catch(fail)} onClose={() => setMovingFolder(null)} />, document.body)}
      {toast && <div className="toast" role="status">{toast.msg}{toast.undo && <> <button className="small" onClick={async () => { const u = toast.undo!; setToast(null); await u(); }}>{T("Annuler", "Undo")}</button></>}</div>}
    </>
  );
}

function FolderMenu({ onRename, onMove, onDelete, T }: { onRename: () => void; onMove: () => void; onDelete: () => void; T: (fr: string, en: string) => string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  const act = (fn: () => void) => (e: React.MouseEvent) => { e.stopPropagation(); setOpen(false); fn(); };
  return (
    <div className="pmenu" ref={box} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <button className="ghost icon-btn pmenu-btn" aria-haspopup="menu" aria-expanded={open} title={T("Plus d’actions", "More actions")} onClick={() => setOpen(!open)}>⋯</button>
      {open && (
        <div className="menu pmenu-list" role="menu">
          <button role="menuitem" onClick={act(onRename)}>✎ {T("Renommer…", "Rename…")}</button>
          <button role="menuitem" onClick={act(onMove)}>📁 {T("Déplacer vers…", "Move to…")}</button>
          <div className="menu-sep" />
          <button role="menuitem" className="danger" onClick={act(onDelete)}>🗑 {T("Supprimer le dossier", "Delete folder")}</button>
        </div>
      )}
    </div>
  );
}
