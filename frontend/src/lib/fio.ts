import { api } from "../api";

/** Safe file name (keeps accents, drops characters that are not allowed in file names). */
export function fioName(name: string) {
  return (name.replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim() || "projet").slice(0, 120) + ".fio";
}

/**
 * Save a project as a .fio file. Where the browser can (Chrome, Edge), the usual "Save as" window lets the user
 * choose the folder and the name; elsewhere the file goes to the downloads. Returns false if the user cancelled.
 */
export async function saveFio(projectId: string, projectName: string): Promise<boolean> {
  type Writable = { write: (b: Blob) => Promise<void>; close: () => Promise<void> };
  type Handle = { createWritable: () => Promise<Writable> };
  const picker = (window as unknown as { showSaveFilePicker?: (o: unknown) => Promise<Handle> }).showSaveFilePicker;
  if (picker && window.self === window.top) {
    let handle: Handle | null;
    try {
      handle = await picker({
        suggestedName: fioName(projectName),
        types: [{ description: "Projet Fiole", accept: { "application/vnd.fiole.project+zip": [".fio"] } }],
      });
    } catch (e) {
      if ((e as DOMException)?.name === "AbortError") return false;
      handle = null; // picker refused (sandbox…): plain download below
    }
    if (handle) {
      const r = await fetch(`/api/projects/${projectId}/archive`, { credentials: "include" });
      if (!r.ok) throw new Error(String(r.status));
      const w = await handle.createWritable();
      await w.write(await r.blob());
      await w.close();
      return true;
    }
  }
  const a = document.createElement("a");
  a.href = `/api/projects/${projectId}/archive`;
  a.download = fioName(projectName);
  document.body.appendChild(a);
  a.click();
  a.remove();
  return true;
}

export interface Opened { id: string; url: string; name: string; styles_added: number }

/** Open a .fio file (or an older .fiole.json export): creates a new project, optionally inside a folder. */
export function openFio(file: File, folderId: string | null): Promise<Opened> {
  const fd = new FormData();
  fd.append("file", file);
  if (folderId) fd.append("folder_id", folderId);
  return api.form<Opened>("/api/projects/import-archive", fd);
}

export const FIO_ACCEPT = ".fio,.json,application/json,application/zip";
