// Anonymous usage counts (page views, analyses) on gab's tools-usage dashboard, like his other tools.
// No email, no cookie here: the sign-in emails are reported by the server only. If the tracker fails, Fiole keeps working.
type Tracker = { run: (o: { genes?: string[]; source?: string; params?: Record<string, unknown> }) => void };
let USAGE: Tracker | null = null;
const URL_ = "https://gabkrouk.github.io/tools-usage/usage.js";

export function startUsage(version: string) {
  import(/* @vite-ignore */ URL_)
    .then((m) => { USAGE = m.usage("fiole", { version }); })
    .catch(() => {});
}

export function countRun(kind: string) {
  try { USAGE?.run({ genes: [], source: "analysis", params: { kind } }); } catch { /* ignore */ }
}
