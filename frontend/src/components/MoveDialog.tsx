import { useEffect, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import Modal from "./Modal";

export interface FolderT { id: string; name: string; parent_id: string | null; n_projects: number; n_folders: number }

/** Folders in tree order (parents before children), with their depth. */
export function folderTree(folders: FolderT[], skip?: string): { f: FolderT; depth: number }[] {
  const kids = new Map<string | null, FolderT[]>();
  for (const f of folders) {
    const k = f.parent_id && folders.some((x) => x.id === f.parent_id) ? f.parent_id : null;
    kids.set(k, [...(kids.get(k) ?? []), f]);
  }
  const out: { f: FolderT; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const f of (kids.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name))) {
      if (f.id === skip) continue; // a folder cannot go inside itself or its subfolders
      out.push({ f, depth });
      walk(f.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** "Move to…": pick one of my folders (or the top level) for a project or a folder. */
export default function MoveDialog({ what, current, skip, onPick, onClose }: {
  what: string;
  current: string | null;
  skip?: string;
  onPick: (folderId: string | null) => Promise<void> | void;
  onClose: () => void;
}) {
  const { lang } = useI18n();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [folders, setFolders] = useState<FolderT[] | null>(null);
  const [sel, setSel] = useState<string | null>(current);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<FolderT[]>("/api/folders").then(setFolders).catch(() => setFolders([])); }, []);

  async function addFolder() {
    if (!newName.trim()) return;
    const f = await api.post<FolderT>("/api/folders", { name: newName.trim(), parent_id: sel });
    setFolders([...(folders ?? []), f]);
    setSel(f.id);
    setNewName("");
  }
  async function go() {
    setBusy(true);
    try { await onPick(sel); onClose(); } finally { setBusy(false); }
  }
  const item = (id: string | null, label: string, depth: number) => (
    <button key={id ?? "root"} type="button" className={`folder-pick ${sel === id ? "active" : ""}`} style={{ paddingLeft: 10 + depth * 18 }}
      onClick={() => setSel(id)} onDoubleClick={() => { setSel(id); }}>
      {id ? "📁" : "🏠"} {label}{id === current && <span className="muted small-text"> · {T("ici actuellement", "current")}</span>}
    </button>
  );
  return (
    <Modal narrow title={T(`Déplacer « ${what} »`, `Move “${what}”`)} onClose={onClose}
      footer={<><button onClick={onClose}>{T("Annuler", "Cancel")}</button>
        <button className="primary" disabled={busy || sel === current} onClick={go}>{T("Déplacer ici", "Move here")}</button></>}>
      <div className="folder-pick-list">
        {item(null, T("Mes projets (premier niveau)", "My projects (top level)"), 0)}
        {folders && folderTree(folders, skip).map(({ f, depth }) => item(f.id, f.name, depth + 1))}
      </div>
      <form className="row tight" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); addFolder(); }}>
        <input className="grow" placeholder={T("Nouveau dossier dans la sélection…", "New folder in the selection…")} value={newName} onChange={(e) => setNewName(e.target.value)} />
        <button disabled={!newName.trim()}>+ {T("Créer", "Create")}</button>
      </form>
    </Modal>
  );
}
