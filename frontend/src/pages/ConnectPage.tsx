// "Se connecter avec le compte Fiole": Régine and GeneCloud send their users here
// (/connect?tool=regine&return=<page URL>). Once signed in, one click sends them back, signed in on the tool.
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { Logo, useAuth } from "../App";
import { useI18n } from "../i18n";

export const TOOL_NAMES: Record<string, string> = { regine: "Régine", genecloud: "GeneCloud" };
export const toolName = (t: string) => TOOL_NAMES[t] || t;

export default function ConnectPage() {
  const { lang } = useI18n();
  const fr = lang === "fr";
  const { user, setUser } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const q = new URLSearchParams(loc.search);
  const tool = q.get("tool") || "";
  const ret = q.get("return") || "";
  const fresh = !!(loc.state as { fresh?: boolean } | null)?.fresh;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [valid, setValid] = useState<boolean | null>(null);

  async function go() {
    setBusy(true);
    setError("");
    try {
      const r = await api.post<{ redirect: string }>("/api/sso/authorize", { tool, return_to: ret });
      window.location.replace(r.redirect);
    } catch (e) {
      setBusy(false);
      setError(e instanceof ApiError ? (e.detail?.code || String(e)) : String(e));
    }
  }

  useEffect(() => {
    api.get(`/api/sso/check?tool=${encodeURIComponent(tool)}&return_to=${encodeURIComponent(ret)}`)
      .then(() => setValid(true))
      .catch(() => setValid(false));
  }, [tool, ret]);

  // just signed in (or created the account) for this tool: go straight back
  useEffect(() => {
    if (valid && fresh) go();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valid, fresh]);

  const name = toolName(tool);
  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="brand"><Logo size={28} /> Fiole</div>
        {valid === false ? (
          <div className="notice error" role="alert">{fr ? "Ce lien de connexion n’est pas valide." : "This sign-in link is not valid."}</div>
        ) : (
          <>
            <h2 style={{ marginTop: 16 }}>{fr ? `Continuer vers ${name}` : `Continue to ${name}`}</h2>
            <p className="muted">
              {fr
                ? `Votre compte Fiole est aussi votre compte ${name} (un seul compte pour Fiole, Régine et GeneCloud).`
                : `Your Fiole account is also your ${name} account (one account for Fiole, Régine and GeneCloud).`}
            </p>
            <p>{fr ? "Connecté en tant que" : "Signed in as"} <b>{user?.display_name}</b> <span className="muted">({user?.email})</span></p>
            {error && <div className="notice error" role="alert">{error}</div>}
            <button className="primary" style={{ width: "100%", justifyContent: "center", marginTop: 6 }} disabled={busy || !valid} onClick={go}>
              {busy ? "…" : fr ? `Continuer vers ${name}` : `Continue to ${name}`}
            </button>
            <p className="small-text muted" style={{ marginTop: 14, textAlign: "center" }}>
              <a href="#" onClick={async (e) => {
                e.preventDefault();
                await api.post("/api/auth/logout").catch(() => {});
                setUser(null);
                nav("/login", { state: { from: loc.pathname + loc.search } });
              }}>{fr ? "Utiliser un autre compte" : "Use another account"}</a>
              {valid && <> · <a href={ret}>{fr ? "Annuler" : "Cancel"}</a></>}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
