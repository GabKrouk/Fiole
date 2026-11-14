import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// @ts-ignore -- node built-in, no @types/node in this project
import { execSync } from "node:child_process";

declare const process: { env: Record<string, string | undefined> };

function build(): string {
  let sha = process.env.RENDER_GIT_COMMIT?.slice(0, 7) ?? "";
  if (!sha) {
    try { sha = execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim(); } catch { sha = ""; }
  }
  const date = new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC";
  return `${sha ? "#" + sha + " · " : ""}${date}`;
}

export default defineConfig({
  plugins: [react()],
  define: { __BUILD__: JSON.stringify(build()) },
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
