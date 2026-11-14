import { applyTheme, getTheme, Theme } from "./theme";
import { createContext, useContext, useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, ApiError, User } from "./api";
import { useI18n } from "./i18n";
import AuthPage from "./pages/AuthPage";
import Dashboard from "./pages/Dashboard";
import ProjectPage from "./pages/ProjectPage";
import ConnectPage from "./pages/ConnectPage";
import { AccountDialog } from "./components/Account";

interface AuthCtx {
  user: User | null;
  setUser: (u: User | null) => void;
}
const AuthContext = createContext<AuthCtx>({ user: null, setUser: () => {} });
export const useAuth = () => useContext(AuthContext);

// Original mark: a glassy blue flask holding a small yellow bar chart, in a circle (same drawing as favicon.svg).
const LOGO_SVG = `<defs><radialGradient id="fbg" cx="0.35" cy="0.3" r="0.8"><stop offset="0" stop-color="#ffffff"/><stop offset="0.6" stop-color="#e3ebff"/><stop offset="1" stop-color="#b9cbf5"/></radialGradient><linearGradient id="fring" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5b8cff"/><stop offset="1" stop-color="#1c3fa8"/></linearGradient><linearGradient id="fglass" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#a9c2ff"/><stop offset="0.45" stop-color="#e4ecff"/><stop offset="1" stop-color="#8eaaf2"/></linearGradient><linearGradient id="fbar" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ffd84a"/><stop offset="0.5" stop-color="#f5b800"/><stop offset="1" stop-color="#c98a00"/></linearGradient><linearGradient id="fglint" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0.85"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs><circle cx="16" cy="16.6" r="14.6" fill="#0b1a4a" opacity="0.18"/><circle cx="16" cy="16" r="14.6" fill="url(#fbg)" stroke="url(#fring)" stroke-width="1.8"/><path d="M13.3 7.3v5.6L8.4 21.6A1.9 1.9 0 0 0 10.1 24.5h11.8a1.9 1.9 0 0 0 1.7-2.9L18.7 12.9V7.3" fill="#1c3fa8" opacity="0.25" transform="translate(0.5 0.7)"/><path d="M12.2 7.3h7.6M13.3 7.3v5.6L8.4 21.6A1.9 1.9 0 0 0 10.1 24.5h11.8a1.9 1.9 0 0 0 1.7-2.9L18.7 12.9V7.3" fill="url(#fglass)" stroke="url(#fring)" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round"/><rect x="11.6" y="18.6" width="2.2" height="4.2" rx="0.6" fill="url(#fbar)"/><rect x="14.9" y="15.6" width="2.2" height="7.2" rx="0.6" fill="url(#fbar)"/><rect x="18.2" y="19.8" width="2.2" height="3" rx="0.6" fill="url(#fbar)"/><path d="M14.4 9v4.2L11 19.2" fill="none" stroke="url(#fglint)" stroke-width="0.9" stroke-linecap="round"/><ellipse cx="11.5" cy="8.6" rx="5" ry="2.6" fill="#fff" opacity="0.35" transform="rotate(-25 11.5 8.6)"/>`;
export function Logo({ size = 26 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" dangerouslySetInnerHTML={{ __html: LOGO_SVG }} />;
}

function ThemeToggle() {
  const { lang } = useI18n();
  const [theme, setTheme] = useState<Theme>(getTheme());
  const next: Theme = theme === "dark" ? "light" : "dark";
  const label = lang === "fr" ? (next === "light" ? "Thème clair" : "Thème sombre") : (next === "light" ? "Light theme" : "Dark theme");
  return (
    <button className="icon-btn" title={label} aria-label={label} onClick={() => { applyTheme(next); setTheme(next); }}>
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}

export function TopBar({ children, right }: { children?: React.ReactNode; right?: React.ReactNode }) {
  const { user, setUser } = useAuth();
  const { t, lang, setLang } = useI18n();
  const nav = useNavigate();
  const [account, setAccount] = useState(false);
  return (
    <>
    <header className="topbar">
      <Link to="/" className="brand">
        <Logo /> Fiole
      </Link>
      {children}
      <div className="spacer" />
      {right}
      <ThemeToggle />
      <button className="ghost small" onClick={() => {
        const l = lang === "fr" ? "en" : "fr";
        setLang(l);
        if (user) api.patch("/api/auth/me", { locale: l }).catch(() => {});
      }} aria-label="Language">
        {t("lang")}
      </button>
      {user && (
        <>
          <button className="ghost small" title={lang === "fr" ? "Mon compte" : "My account"} onClick={() => setAccount(true)}>👤 {user.display_name}</button>
          <button className="small" onClick={async () => {
            await api.post("/api/auth/logout").catch(() => {});
            setUser(null);
            nav("/login");
          }}>
            {t("signOut")}
          </button>
        </>
      )}
    </header>
    {account && <AccountDialog onClose={() => setAccount(false)} />}
    </>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const { setLang } = useI18n();

  useEffect(() => {
    api.get<User>("/api/auth/me")
      .then((u) => {
        setUser(u);
        try {
          if (!localStorage.getItem("fiole.lang")) setLang(u.locale);
        } catch {
          /* ignore */
        }
      })
      .catch((e) => {
        if (!(e instanceof ApiError)) console.error(e);
      })
      .finally(() => setLoading(false));
  }, [setLang]);

  if (loading) return null;
  return (
    <AuthContext.Provider value={{ user, setUser }}>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" /> : <AuthPage />} />
        <Route path="/" element={user ? <Dashboard /> : <Navigate to="/login" />} />
        <Route path="/p/:projectId/*" element={user ? <ProjectPage /> : <Navigate to="/login" state={{ from: location.pathname + location.search }} />} />
        <Route path="/connect" element={user ? <ConnectPage /> : <Navigate to="/login" state={{ from: location.pathname + location.search }} />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </AuthContext.Provider>
  );
}
