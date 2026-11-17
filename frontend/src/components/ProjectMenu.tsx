import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../App";
import { useI18n } from "../i18n";
import { saveFio } from "../lib/fio";
import MoveDialog from "./MoveDialog";

export interface MenuProject {
  id: string;
  name: string;
  role: string | null;
  via_link?: boolean;
  folder_id?: string | null;
}

/**
 * The "⋯" menu of a project, on the dashboard cards and in the project window:
 * open in a new tab, rename, share, duplicate, save as a .fio file, move to a folder, and move to the trash (owner)
 * or remove from my list (shared with me). Moving to the trash can be undone.
 */
export default function ProjectMenu({ project, inProject, onChanged, onShare, toast }: {
  project: MenuProject;
  inProject?: boolean;
  onChanged?: () => void;
  onShare?: () => void;
  toast: (msg: string, undo?: () => void) => void;
}) {
  const { lang } = useI18n();
  const { user } = useAuth();
  const nav = useNavigate();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [open, setOpen] = useState(false);
  const [moving, setMoving] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const role = project.role ?? "viewer";
  const isOwner = role === "owner";
  const canEdit = isOwner || role === "editor";

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); };
  }, [open]);

  const fail = (e: unknown) => toast(e instanceof ApiError ? T("Action impossible.", "Could not do that.") : String(e));
  const act = (fn: () => Promise<void> | void) => async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setOpen(false);
    try { await fn(); } catch (err) { fail(err); }
  };

  async function rename() {
    const name = window.prompt(T("Nouveau nom du projet", "New project name"), project.name);
    if (!name || !name.trim() || name.trim() === project.name) return;
    await api.patch(`/api/projects/${project.id}`, { name: name.trim() });
    onChanged?.();
  }
  async function duplicate() {
    toast(T("Copie en cours…", "Copying…"));
    const r = await api.post<{ url: string }>(`/api/projects/${project.id}/duplicate`);
    nav(r.url);
  }
  async function save() {
    if (await saveFio(project.id, project.name)) toast(T(`« ${project.name} » est enregistré dans un fichier .fio.`, `“${project.name}” saved to a .fio file.`));
  }
  async function moveTo(folderId: string | null) {
    await api.put(`/api/projects/${project.id}/folder`, { folder_id: folderId });
    toast(folderId ? T("Projet rangé dans le dossier.", "Project moved to the folder.") : T("Projet remis au premier niveau.", "Project moved to the top level."));
    onChanged?.();
  }
  async function trash() {
    await api.del(`/api/projects/${project.id}`);
    const undo = async () => {
      await api.post(`/api/projects/${project.id}/restore`);
      if (inProject) nav(`/p/${project.id}`); else onChanged?.();
    };
    if (inProject) return nav("/", { state: { trashed: { id: project.id, name: project.name } } }); // the dashboard offers "Undo"
    onChanged?.();
    toast(T(`« ${project.name} » est dans la corbeille.`, `“${project.name}” moved to the trash.`), undo);
  }
  async function leave() {
    if (!user) return;
    if (!window.confirm(T(`Retirer « ${project.name} » de votre liste ? Vous perdrez votre accès (le propriétaire pourra vous le redonner).`,
      `Remove “${project.name}” from your list? You will lose access (the owner can give it back).`))) return;
    await api.del(`/api/projects/${project.id}/members/${user.id}`);
    if (inProject) nav("/"); else onChanged?.();
  }

  return (
    <div className="pmenu" ref={box} onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}>
      <button className="ghost icon-btn pmenu-btn" aria-haspopup="menu" aria-expanded={open} title={T("Plus d’actions", "More actions")}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(!open); }}>⋯</button>
      {open && (
        <div className="menu pmenu-list" role="menu">
          {!inProject && <button role="menuitem" onClick={act(() => { window.open(`/p/${project.id}`, "_blank"); })}>↗ {T("Ouvrir dans un nouvel onglet", "Open in a new tab")}</button>}
          {canEdit && <button role="menuitem" onClick={act(rename)}>✎ {T("Renommer…", "Rename…")}</button>}
          <button role="menuitem" onClick={act(() => (onShare ? onShare() : nav(`/p/${project.id}?share=1`)))}>🔒 {T("Partager…", "Share…")}</button>
          <button role="menuitem" onClick={act(duplicate)}>⧉ {T("Dupliquer le projet", "Duplicate project")}</button>
          <button role="menuitem" onClick={act(save)}>💾 {T("Enregistrer dans un fichier .fio", "Save to a .fio file")}</button>
          {!project.via_link && <button role="menuitem" onClick={act(() => setMoving(true))}>📁 {T("Déplacer vers un dossier…", "Move to a folder…")}</button>}
          {isOwner
            ? <><div className="menu-sep" /><button role="menuitem" className="danger" onClick={act(trash)}>🗑 {T("Mettre à la corbeille", "Move to trash")}</button></>
            : !project.via_link && <><div className="menu-sep" /><button role="menuitem" className="danger" onClick={act(leave)}>⊘ {T("Retirer de ma liste", "Remove from my list")}</button></>}
        </div>
      )}
      {moving && createPortal(
        <div onClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.stopPropagation()}>
          <MoveDialog what={project.name} current={project.folder_id ?? null} onPick={(f) => moveTo(f).catch(fail)} onClose={() => setMoving(false)} />
        </div>, document.body)}
    </div>
  );
}
