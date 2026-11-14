export type Theme = "dark" | "light";

/** Dark slate is the default look; the choice is remembered per browser. */
export function getTheme(): Theme {
  try {
    const t = localStorage.getItem("fiole-theme");
    if (t === "light" || t === "dark") return t;
  } catch { /* storage unavailable */ }
  return "dark";
}

export function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("fiole-theme", t); } catch { /* ignore */ }
}

declare const __BUILD__: string;
export const BUILD = typeof __BUILD__ === "string" ? __BUILD__ : "dev";
