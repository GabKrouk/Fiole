import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, User } from "../api";
import { TopBar, useAuth } from "../App";
import { useI18n } from "../i18n";
import Modal from "./Modal";

const fmtDate = (s: string | null, lang: string) => (s ? new Date(s).toLocaleDateString(lang === "fr" ? "fr-FR" : "en-GB", { dateStyle: "long" }) : "");

/** "My account": name and the donation-appeal consent (can be changed at any time). */
export function AccountDialog({ onClose }: { onClose: () => void }) {
  const { lang } = useI18n();
  const { user, setUser } = useAuth();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [name, setName] = useState(user?.display_name ?? "");
  if (!user) return null;
  const setConsent = async (v: boolean) => setUser(await api.patch<User>("/api/auth/me", { donation_opt_in: v }));
  return (
    <Modal title={T("Mon compte", "My account")} onClose={onClose} narrow
      footer={<button className="primary" onClick={async () => { if (name.trim() && name !== user.display_name) setUser(await api.patch<User>("/api/auth/me", { display_name: name })); onClose(); }}>{T("OK", "Done")}</button>}>
      <label className="field"><span>{T("Nom affiché", "Display name")}</span><input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <div className="field"><span>Email</span><div>{user.email}</div></div>
      <label className="check consent">
        <input type="checkbox" checked={!!user.donation_opt_in} onChange={(e) => setConsent(e.target.checked)} />
        <span>{T("Recevoir l’appel aux dons annuel", "Receive the yearly donation appeal")}<br />
          <span className="small-text muted">{user.donation_opt_in === null ? T("Vous n’avez pas encore répondu.", "You have not answered yet.")
            : user.donation_opt_in ? T(`Accepté le ${fmtDate(user.donation_opt_in_at, lang)}.`, `Accepted on ${fmtDate(user.donation_opt_in_at, lang)}.`)
            : T(`Refusé le ${fmtDate(user.donation_opt_in_at, lang)}. Nous ne vous écrirons pas pour des dons.`, `Declined on ${fmtDate(user.donation_opt_in_at, lang)}. We will not write to you about donations.`)}</span></span>
      </label>
      {user.is_admin && <p style={{ marginTop: 12 }}><Link to="/admin" onClick={onClose}>⚙ {T("Administration de Fiole", "Fiole administration")}</Link></p>}
    </Modal>
  );
}

/** Asked once to people who signed up before the opt-in existed. Nothing is assumed: no answer = not opted in. */
export function ConsentBanner() {
  const { lang } = useI18n();
  const { user, setUser } = useAuth();
  const [hidden, setHidden] = useState(() => { try { return sessionStorage.getItem("fiole-consent-later") === "1"; } catch { return false; } });
  if (!user || user.donation_opt_in !== null || hidden) return null;
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const answer = async (v: boolean) => setUser(await api.patch<User>("/api/auth/me", { donation_opt_in: v }));
  return (
    <div className="consent-banner" role="region" aria-label={T("Appel aux dons", "Donation appeal")}>
      <span>💚 {T("Fiole est gratuit. Acceptez-vous de recevoir, au plus une fois par an, notre appel aux dons par email ?",
        "Fiole is free. Would you like to receive our donation appeal by email, at most once a year?")}</span>
      <button className="small primary" onClick={() => answer(true)}>{T("Oui, volontiers", "Yes, sure")}</button>
      <button className="small" onClick={() => answer(false)}>{T("Non merci", "No thanks")}</button>
      <button className="ghost small" onClick={() => { try { sessionStorage.setItem("fiole-consent-later", "1"); } catch { /* ignore */ } setHidden(true); }}>{T("Plus tard", "Later")}</button>
    </div>
  );
}

interface Summary { accounts: number; donation_opt_in: number; donation_declined: number; donation_not_asked: number; active_30_days: number; projects: number; email_configured: boolean; email_provider: string | null; usage_dashboard: boolean }

/** Admin page (accounts listed in FIOLE_ADMIN_EMAILS): counts, and the CSV of people who opted in to the donation appeal. */
export function AdminPage() {
  const { lang } = useI18n();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [s, setS] = useState<Summary | null>(null);
  const [denied, setDenied] = useState(false);
  useEffect(() => { api.get<Summary>("/api/admin/summary").then(setS).catch(() => setDenied(true)); }, []);
  return (
    <>
      <TopBar />
      <main className="page">
        <h1>{T("Administration", "Administration")}</h1>
        {denied && <div className="notice error">{T("Réservé aux administrateurs.", "Administrators only.")}</div>}
        {s && <>
          <div className="stat-grid">
            <div className="stat"><b>{s.accounts}</b><span>{T("comptes", "accounts")}</span></div>
            <div className="stat"><b>{s.active_30_days}</b><span>{T("connectés sur 30 jours", "signed in, last 30 days")}</span></div>
            <div className="stat"><b>{s.donation_opt_in}</b><span>{T("acceptent l’appel aux dons", "accept the donation appeal")}</span></div>
            <div className="stat"><b>{s.donation_declined}</b><span>{T("ont refusé", "declined")}</span></div>
            <div className="stat"><b>{s.donation_not_asked}</b><span>{T("pas encore répondu", "not answered yet")}</span></div>
            <div className="stat"><b>{s.projects}</b><span>{T("projets", "projects")}</span></div>
          </div>
          <div className="panel" style={{ marginTop: 16 }}>
            <h3>{T("Appel aux dons annuel", "Yearly donation appeal")}</h3>
            <p className="muted">{T("Le fichier ne contient que les personnes qui ont coché « Recevoir l’appel aux dons annuel », avec la date de leur accord. N’écrivez jamais aux autres (RGPD).",
              "The file only holds people who ticked “Receive the yearly donation appeal”, with the date they agreed. Never write to the others (GDPR).")}</p>
            <a className="btn primary" href="/api/admin/donation-optins.csv">⤓ {T("Télécharger les emails (CSV)", "Download the emails (CSV)")}</a>
          </div>
          <div className="panel" style={{ marginTop: 16 }}>
            <h3>{T("Tableau de bord de mes outils (tools-usage)", "My tools dashboard (tools-usage)")}</h3>
            <p className={s.usage_dashboard ? "" : "muted"}>{s.usage_dashboard
              ? <>{T("Relié : chaque connexion envoie l’email du compte au tableau de bord (section « Comptes utilisateurs », visible avec la clé admin).", "Connected: each sign-in sends the account email to the dashboard (“Comptes utilisateurs” section, visible with the admin key).")} <a href="https://gabkrouk.github.io/tools-usage/" target="_blank" rel="noopener">gabkrouk.github.io/tools-usage</a></>
              : T("Pas encore relié : ajoutez FIOLE_USAGE_KEY (la clé admin du tableau de bord) dans Render. Les vues et analyses anonymes y sont déjà comptées.", "Not connected yet: add FIOLE_USAGE_KEY (the dashboard admin key) in Render. Anonymous views and analyses are already counted there.")}</p>
          </div>
          <div className="panel" style={{ marginTop: 16 }}>
            <h3>{T("Envoi d’emails", "Email sending")}</h3>
            <p className={s.email_configured ? "" : "muted"}>{s.email_configured
              ? T(`Configuré (${s.email_provider}). Les notifications de partage partent.`, `Configured (${s.email_provider}). Sharing notifications are sent.`)
              : T("Pas encore configuré : aucune notification n’est envoyée (le partage marche quand même, il faut envoyer le lien soi-même).",
                "Not configured yet: no notification is sent (sharing still works; send the link yourself).")}</p>
          </div>
        </>}
      </main>
    </>
  );
}
