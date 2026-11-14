import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { TopBar, useAuth } from "../App";
import { useI18n } from "../i18n";

/** Shown when a project link does not open: no access (or no such project). Lets the person ask for access. */
export default function NoAccess({ projectId }: { projectId: string }) {
  const { lang } = useI18n();
  const { user } = useAuth();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [status, setStatus] = useState<string | null | undefined>(undefined);
  const [message, setMessage] = useState("");
  const [role, setRole] = useState<"viewer" | "editor">("viewer");
  const [err, setErr] = useState("");

  useEffect(() => {
    api.get<{ status: string | null }>(`/api/projects/${projectId}/access-requests/mine`).then((r) => setStatus(r.status)).catch(() => setStatus(null));
  }, [projectId]);

  async function send() {
    setErr("");
    try {
      await api.post(`/api/projects/${projectId}/access-requests`, { message, role });
      setStatus("pending");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setErr(T("Ce projet n’existe pas (ou a été supprimé).", "This project does not exist (or was deleted)."));
      else if (e instanceof ApiError && e.status === 409) window.location.reload();
      else setErr(e instanceof ApiError ? (e.detail?.message?.[lang] ?? JSON.stringify(e.detail)) : String(e));
    }
  }

  return (
    <>
      <TopBar />
      <main className="page">
        <div className="auth-card" style={{ margin: "40px auto", maxWidth: 520 }}>
          <h2>🔒 {T("Vous n’avez pas accès à ce projet", "You need access to this project")}</h2>
          <p className="muted">{T(`Demandez l’accès à son propriétaire, ou connectez-vous avec un autre compte (vous êtes connecté en tant que ${user?.email}).`,
            `Ask its owner for access, or switch to another account (you are signed in as ${user?.email}).`)}</p>
          {status === "pending" ? (
            <div className="notice info">{T("Demande envoyée. Vous pourrez ouvrir le projet dès que le propriétaire l’aura acceptée (rechargez cette page).",
              "Request sent. You can open the project as soon as the owner approves it (reload this page).")}</div>
          ) : status !== undefined && <>
            {status === "denied" && <div className="notice warn">{T("Votre demande précédente a été refusée. Vous pouvez en envoyer une nouvelle.", "Your previous request was denied. You can send a new one.")}</div>}
            <label className="field"><span>{T("Accès demandé", "Access requested")}</span>
              <select value={role} onChange={(e) => setRole(e.target.value as "viewer" | "editor")}>
                <option value="viewer">{T("Lecteur (consulter)", "Viewer (view)")}</option>
                <option value="editor">{T("Éditeur (modifier)", "Editor (edit)")}</option>
              </select></label>
            <label className="field"><span>{T("Message (facultatif)", "Message (optional)")}</span>
              <textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} /></label>
            <button className="primary" onClick={send}>{T("Demander l’accès", "Request access")}</button>
          </>}
          {err && <div className="notice error" style={{ marginTop: 10 }}>{err}</div>}
          <p style={{ marginTop: 16 }}><Link to="/">← {T("Mes projets", "My projects")}</Link></p>
        </div>
      </main>
    </>
  );
}
