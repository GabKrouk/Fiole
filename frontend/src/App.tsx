import { createContext, useContext, useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, ApiError, User } from "./api";
import { useI18n } from "./i18n";
import AuthPage from "./pages/AuthPage";
import Dashboard from "./pages/Dashboard";
import ProjectPage from "./pages/ProjectPage";

interface AuthCtx {
  user: User | null;
  setUser: (u: User | null) => void;
}
const AuthContext = createContext<AuthCtx>({ user: null, setUser: () => {} });
export const useAuth = () => useContext(AuthContext);

export function Logo({ size = 22 }: { size?: number }) {
  // Original mark: a flask whose contents form a small bar chart.
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 2.5h6M10 2.5v6.2L4.6 18.6A2 2 0 0 0 6.4 21.5h11.2a2 2 0 0 0 1.8-2.9L14 8.7V2.5" fill="none" stroke="#0d6b6e" strokeWidth="1.7" strokeLinejoin="round" />
      <rect x="8" y="15" width="2" height="4.5" rx="0.5" fill="#0d6b6e" />
      <rect x="11" y="12.5" width="2" height="7" rx="0.5" fill="#e09a1a" />
      <rect x="14" y="16.2" width="2" height="3.3" rx="0.5" fill="#0d6b6e" />
    </svg>
  );
}

export function TopBar({ children, right }: { children?: React.ReactNode; right?: React.ReactNode }) {
  const { user, setUser } = useAuth();
  const { t, lang, setLang } = useI18n();
  const nav = useNavigate();
  return (
    <header className="topbar">
      <Link to="/" className="brand">
        <Logo /> Fiole
      </Link>
      {children}
      <div className="spacer" />
      {right}
      <button className="ghost small" onClick={() => {
        const l = lang === "fr" ? "en" : "fr";
        setLang(l);
        if (user) api.patch("/api/auth/me", { locale: l }).catch(() => {});
      }} aria-label="Language">
        {t("lang")}
      </button>
      {user && (
        <>
          <span className="muted small-text">{user.display_name}</span>
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
        <Route path="/p/:projectId/*" element={user ? <ProjectPage /> : <Navigate to="/login" state={{ from: location.pathname }} />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </AuthContext.Provider>
  );
}
