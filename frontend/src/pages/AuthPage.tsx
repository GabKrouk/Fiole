import { FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, User } from "../api";
import { Logo, useAuth } from "../App";
import { useI18n } from "../i18n";
import { toolName } from "./ConnectPage";

export default function AuthPage() {
  const { t, lang, setLang } = useI18n();
  const { setUser } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const from = (loc.state as { from?: string } | null)?.from || "";
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const u =
        mode === "login"
          ? await api.post<User>("/api/auth/login", { email, password })
          : await api.post<User>("/api/auth/register", { email, password, display_name: name || email.split("@")[0], locale: lang });
      setUser(u);
      nav(from || "/", { state: { fresh: from.startsWith("/connect") } });
    } catch (err) {
      const code = err instanceof ApiError ? err.detail?.code : "";
      setError(code === "bad_credentials" ? t("badCredentials") : code === "email_taken" ? t("emailTaken") : `${t("error")}: ${code || String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <form className="auth-card" onSubmit={submit}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="brand"><Logo size={28} /> Fiole</div>
          <button type="button" className="ghost small" onClick={() => setLang(lang === "fr" ? "en" : "fr")}>{t("lang")}</button>
        </div>
        <p className="muted" style={{ marginBottom: 20 }}>{t("appTagline")}</p>
        {from.startsWith("/connect") && (() => {
          const n = toolName(new URLSearchParams(from.split("?")[1] || "").get("tool") || "");
          return <div className="notice info">{lang === "fr" ? `Connectez-vous (ou créez un compte gratuit) pour utiliser ${n}. Le même compte sert pour Fiole, Régine et GeneCloud.` : `Sign in (or create a free account) to use ${n}. The same account works for Fiole, Régine and GeneCloud.`}</div>;
        })()}
        {from.startsWith("/p/") && <div className="notice info">{lang === "fr" ? "Connectez-vous (ou créez un compte gratuit) pour ouvrir le projet qu’on vous a partagé." : "Sign in (or create a free account) to open the project shared with you."}</div>}
        <h2>{mode === "login" ? t("signIn") : t("signUp")}</h2>
        {mode === "register" && (
          <label className="field"><span>{t("displayName")}</span><input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" /></label>
        )}
        <label className="field"><span>{t("email")}</span><input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></label>
        <label className="field"><span>{t("password")}</span><input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
        {error && <div className="notice error" role="alert">{error}</div>}
        {mode === "register" && <p className="small-text muted">{lang === "fr" ? "Votre email sert à votre compte (Fiole, Régine, GeneCloud), au partage, et au suivi d’utilisation de ces outils. Il n’est jamais vendu ni transmis." : "Your email is used for your account (Fiole, Régine, GeneCloud), sharing, and usage statistics of these tools. It is never sold or passed on."}</p>}
        <button className="primary" style={{ width: "100%", justifyContent: "center", marginTop: 6 }} disabled={busy}>
          {mode === "login" ? t("signIn") : t("signUp")}
        </button>
        <p className="small-text muted" style={{ marginTop: 14, textAlign: "center" }}>
          {mode === "login" ? t("noAccount") : t("haveAccount")}{" "}
          <a href="#" onClick={(e) => { e.preventDefault(); setMode(mode === "login" ? "register" : "login"); setError(""); }}>
            {mode === "login" ? t("signUp") : t("signIn")}
          </a>
        </p>
      </form>
    </div>
  );
}
