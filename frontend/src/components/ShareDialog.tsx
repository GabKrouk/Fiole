import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useI18n } from "../i18n";
import Modal from "./Modal";

interface Member { user_id: string; email: string; display_name: string; role: string; you: boolean }
interface Invite { id: number; email: string; role: string }
interface Req { id: number; user_id: string; email: string; display_name: string; role: string; message: string }
export interface Sharing {
  general_access: "restricted" | "link_viewer" | "link_editor" | "link_commenter";
  editors_can_share: boolean;
  can_manage: boolean;
  is_owner: boolean;
  my_role: string;
  members: Member[];
  invites: Invite[];
  requests: Req[];
  email_configured: boolean;
}

function Avatar({ name }: { name: string }) {
  const ch = (name || "?").trim()[0]?.toUpperCase() ?? "?";
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return <span className="avatar" style={{ background: `hsl(${h} 55% 45%)` }} aria-hidden>{ch}</span>;
}

/** Google-Docs-style share dialog: people with access, invitations, access requests, general access, copy link. */
export default function ShareDialog({ projectId, projectName, onClose, onLeft }: { projectId: string; projectName: string; onClose: () => void; onLeft: () => void }) {
  const { lang } = useI18n();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [s, setS] = useState<Sharing | null>(null);
  const [emails, setEmails] = useState("");
  const [role, setRole] = useState<"viewer" | "editor">("viewer");
  const [notify, setNotify] = useState(true);
  const [message, setMessage] = useState("");
  const [err, setErr] = useState("");
  const [info, setInfo] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const url = `${window.location.origin}/p/${projectId}`;
  const base = `/api/projects/${projectId}`;
  const fail = (e: unknown) => setErr(e instanceof ApiError ? (e.detail?.message?.[lang] ?? JSON.stringify(e.detail)) : String(e));
  const roleLabel = (r: string) => ({ owner: T("Propriétaire", "Owner"), editor: T("Éditeur", "Editor"), viewer: T("Lecteur", "Viewer"), commenter: T("Commentateur", "Commenter") }[r] ?? r);

  useEffect(() => { api.get<Sharing>(`${base}/sharing`).then(setS).catch(fail); }, [base]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(p: Promise<Sharing>) {
    setErr("");
    try { setS(await p); } catch (e) { fail(e); }
  }

  async function add() {
    const list = emails.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
    if (!list.length) return;
    setBusy(true); setErr(""); setInfo("");
    try {
      const r = await api.post<{ added: string[]; invited: string[]; updated: string[]; email: Record<string, string>; sharing: Sharing }>(`${base}/members`, { emails: list, role, notify, message });
      setS(r.sharing);
      setEmails(""); setMessage("");
      const parts: string[] = [];
      if (r.added.length) parts.push(T(`Accès donné à ${r.added.join(", ")}.`, `Access given to ${r.added.join(", ")}.`));
      if (r.updated.length) parts.push(T(`Rôle modifié pour ${r.updated.join(", ")}.`, `Role changed for ${r.updated.join(", ")}.`));
      if (r.invited.length) parts.push(T(`${r.invited.join(", ")} n’a pas encore de compte : l’accès s’ouvrira dès son inscription avec cette adresse.`,
        `${r.invited.join(", ")} has no account yet: access opens as soon as they sign up with this address.`));
      const st = Object.values(r.email);
      if (notify && st.length) {
        if (st.every((x) => x === "sent")) parts.push(T("Email de notification envoyé.", "Notification email sent."));
        else if (st.includes("not_configured")) parts.push(T("Aucun email envoyé : l’envoi d’emails n’est pas encore configuré sur le serveur. Envoyez-leur le lien.", "No email sent: email sending is not configured on the server yet. Send them the link."));
        else parts.push(T("L’email de notification n’a pas pu partir. Envoyez-leur le lien.", "The notification email could not be sent. Send them the link."));
      }
      setInfo(parts.join(" "));
    } catch (e) { fail(e); } finally { setBusy(false); }
  }

  async function leave(m: Member) {
    if (!window.confirm(T(`Quitter « ${projectName} » ? Il disparaîtra de « Partagés avec moi ».`, `Leave “${projectName}”? It will disappear from “Shared with me”.`))) return;
    try { await api.del(`${base}/members/${m.user_id}`); onLeft(); } catch (e) { fail(e); }
  }

  const ga = s?.general_access ?? "restricted";
  const manage = !!s?.can_manage;

  return (
    <Modal title={T(`Partager « ${projectName} »`, `Share “${projectName}”`)} onClose={onClose} narrow
      footer={<>
        <button onClick={() => { navigator.clipboard?.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>🔗 {copied ? T("Lien copié", "Link copied") : T("Copier le lien", "Copy link")}</button>
        <span className="grow" />
        <button className="primary" onClick={onClose}>{T("OK", "Done")}</button></>}>
      {!s ? <div className="muted">…</div> : <>
        {manage && <div className="share-add">
          <div className="row">
            <input className="grow" value={emails} placeholder={T("Ajouter des personnes (emails)", "Add people (emails)")} aria-label={T("Emails", "Emails")}
              onChange={(e) => setEmails(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
            <select value={role} aria-label={T("Rôle", "Role")} onChange={(e) => setRole(e.target.value as "viewer" | "editor")}>
              <option value="viewer">{roleLabel("viewer")}</option><option value="editor">{roleLabel("editor")}</option>
            </select>
          </div>
          {emails.trim() && <>
            <label className="check" style={{ marginTop: 8 }}><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} /> {T("Prévenir par email", "Notify by email")}
              {!s.email_configured && <span className="small-text muted"> ({T("envoi d’emails pas encore configuré", "email sending not configured yet")})</span>}</label>
            {notify && <textarea rows={2} value={message} placeholder={T("Message (facultatif)", "Message (optional)")} onChange={(e) => setMessage(e.target.value)} style={{ width: "100%" }} />}
            <div className="row" style={{ justifyContent: "flex-end", marginTop: 6 }}><button className="primary" disabled={busy} onClick={add}>{T("Partager", "Share")}</button></div>
          </>}
        </div>}
        {info && <div className="notice info">{info}</div>}
        {err && <div className="notice error">{err}</div>}

        {manage && s.requests.length > 0 && <>
          <h4 className="share-h">{T("Demandes d’accès", "Access requests")}</h4>
          {s.requests.map((r) => <RequestRow key={r.id} r={r} roleLabel={roleLabel} T={T}
            onApprove={(role) => run(api.post<Sharing>(`${base}/access-requests/${r.id}/approve`, { role }))}
            onDeny={() => run(api.post<Sharing>(`${base}/access-requests/${r.id}/deny`))} />)}
        </>}

        <h4 className="share-h">{T("Personnes ayant accès", "People with access")}</h4>
        <div className="share-list">
          {s.members.map((m) => (
            <div key={m.user_id} className="share-person">
              <Avatar name={m.display_name} />
              <div className="grow"><div>{m.display_name}{m.you && <span className="muted"> ({T("vous", "you")})</span>}</div><div className="small-text muted">{m.email}</div></div>
              {m.role === "owner" ? <span className="muted small-text">{roleLabel("owner")}</span>
                : manage && !m.you ? (
                  <select value={m.role} aria-label={T(`Rôle de ${m.email}`, `Role of ${m.email}`)} onChange={(e) => {
                    if (e.target.value === "__remove") run(api.del<Sharing>(`${base}/members/${m.user_id}`));
                    else run(api.patch<Sharing>(`${base}/members/${m.user_id}`, { role: e.target.value }));
                  }}>
                    <option value="viewer">{roleLabel("viewer")}</option><option value="editor">{roleLabel("editor")}</option>
                    <option value="__remove">{T("Retirer l’accès", "Remove access")}</option>
                  </select>)
                : m.you ? <span className="row tight"><span className="muted small-text">{roleLabel(m.role)}</span><button className="small" onClick={() => leave(m)}>{T("Quitter", "Leave")}</button></span>
                : <span className="muted small-text">{roleLabel(m.role)}</span>}
            </div>
          ))}
          {manage && s.invites.map((i) => (
            <div key={i.id} className="share-person">
              <Avatar name={i.email} />
              <div className="grow"><div>{i.email}</div><div className="small-text muted">{T("Invitation en attente (pas encore de compte)", "Pending invitation (no account yet)")}</div></div>
              <select value={i.role} aria-label={T(`Rôle de ${i.email}`, `Role of ${i.email}`)} onChange={(e) => {
                if (e.target.value === "__remove") run(api.del<Sharing>(`${base}/invites/${i.id}`));
                else run(api.patch<Sharing>(`${base}/invites/${i.id}`, { role: e.target.value }));
              }}>
                <option value="viewer">{roleLabel("viewer")}</option><option value="editor">{roleLabel("editor")}</option>
                <option value="__remove">{T("Annuler l’invitation", "Cancel invitation")}</option>
              </select>
            </div>
          ))}
        </div>

        <h4 className="share-h">{T("Accès général", "General access")}</h4>
        <div className="share-person">
          <span className="avatar" style={{ background: ga === "restricted" ? "var(--surface-2)" : "var(--ok-soft)", color: ga === "restricted" ? "var(--ink-2)" : "var(--ok)" }} aria-hidden>{ga === "restricted" ? "🔒" : "🌐"}</span>
          <div className="grow">
            {manage ? (
              <select value={ga === "link_commenter" ? "link_viewer" : ga} aria-label={T("Accès général", "General access")}
                onChange={(e) => run(api.put<Sharing>(`${base}/access`, { general_access: e.target.value }))}>
                <option value="restricted">{T("Limité", "Restricted")}</option>
                <option value="link_viewer">{T("Tous les utilisateurs disposant du lien — lecteur", "Anyone with the link — viewer")}</option>
                <option value="link_editor">{T("Tous les utilisateurs disposant du lien — éditeur", "Anyone with the link — editor")}</option>
              </select>
            ) : <div>{ga === "restricted" ? T("Limité", "Restricted") : T("Tous les utilisateurs disposant du lien", "Anyone with the link")}</div>}
            <div className="small-text muted">{ga === "restricted"
              ? T("Seules les personnes ajoutées peuvent ouvrir ce projet avec le lien.", "Only people added can open this project with the link.")
              : T(`Toute personne connectée à Fiole qui a le lien peut l’ouvrir (${ga === "link_editor" ? "et le modifier" : "en lecture seule"}). Jamais sans compte.`,
                `Anyone signed in to Fiole who has the link can open it (${ga === "link_editor" ? "and edit it" : "read-only"}). Never without an account.`)}</div>
          </div>
        </div>
        {s.is_owner && <label className="check" style={{ marginTop: 10 }}>
          <input type="checkbox" checked={s.editors_can_share} onChange={(e) => run(api.put<Sharing>(`${base}/access`, { editors_can_share: e.target.checked }))} />
          {T("Les éditeurs peuvent modifier les autorisations et partager", "Editors can change permissions and share")}</label>}
        {!manage && <p className="small-text muted" style={{ marginTop: 10 }}>{T("Seul le propriétaire peut modifier le partage.", "Only the owner can change sharing.")}</p>}
      </>}
    </Modal>
  );
}

function RequestRow({ r, roleLabel, T, onApprove, onDeny }: { r: Req; roleLabel: (r: string) => string; T: (fr: string, en: string) => string; onApprove: (role: string) => void; onDeny: () => void }) {
  const [role, setRole] = useState(r.role === "editor" ? "editor" : "viewer");
  return (
    <div className="share-person request">
      <Avatar name={r.display_name} />
      <div className="grow">
        <div>{r.display_name} <span className="small-text muted">{r.email}</span></div>
        <div className="small-text muted">{T(`Demande l’accès ${roleLabel(r.role).toLowerCase()}`, `Asks for ${roleLabel(r.role).toLowerCase()} access`)}{r.message && <> · « {r.message} »</>}</div>
      </div>
      <select value={role} aria-label={T("Rôle", "Role")} onChange={(e) => setRole(e.target.value)}>
        <option value="viewer">{roleLabel("viewer")}</option><option value="editor">{roleLabel("editor")}</option>
      </select>
      <button className="small primary" onClick={() => onApprove(role)}>{T("Accepter", "Approve")}</button>
      <button className="small" onClick={onDeny}>{T("Refuser", "Deny")}</button>
    </div>
  );
}
