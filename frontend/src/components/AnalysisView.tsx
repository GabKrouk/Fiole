import { useEffect, useMemo, useRef, useState } from "react";
import { countRun } from "../usage";
import { useNavigate, useParams } from "react-router-dom";
import { Analysis, api, ApiError, Dataset, TableContent } from "../api";
import { useI18n } from "../i18n";
import type { ProjectCtx } from "../pages/ProjectPage";
import ResultView from "./ResultView";
import { factorColumns, levels, numericColumns } from "../columns";

export function groupNames(c: TableContent | undefined, cfg?: Record<string, any>): string[] {
  if (!c) return [];
  if (cfg?.value_col) {
    if (cfg.group_col) return levels(c, cfg.group_col);
    return [c.columns.find((x) => x.id === cfg.value_col)?.name ?? ""];
  }
  const d = c.design || { layout: "wide" };
  if (d.layout === "wide") {
    const ids = d.group_columns || c.columns.filter((x) => x.type === "numeric" && x.id !== d.subject).map((x) => x.id);
    return ids.map((id) => c.columns.find((x) => x.id === id)?.name).filter(Boolean) as string[];
  }
  if (!d.group) return [];
  const out: string[] = [];
  for (const r of c.rows) {
    const v = r.cells[d.group];
    if (v === null || v === undefined) continue;
    const s = typeof v === "number" && Number.isInteger(v) ? String(v) : String(v);
    if (!out.includes(s)) out.push(s);
  }
  return out;
}

export default function AnalysisView({ ctx }: { ctx: ProjectCtx }) {
  const { analysisId } = useParams();
  const { t, L, lang } = useI18n();
  const nav = useNavigate();
  const pid = ctx.project.id;
  const canEdit = ctx.project.permissions.includes("edit");
  const fromProject = ctx.project.analyses.find((a) => a.id === analysisId);
  const [a, setA] = useState<Analysis | null>(fromProject ?? null);
  const [ds, setDs] = useState<Dataset | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const versionRef = useRef(fromProject?.version ?? 0);
  const timer = useRef<number>();

  useEffect(() => {
    const fresh = ctx.project.analyses.find((x) => x.id === analysisId) ?? null;
    if (fresh && (!a || fresh.id !== a.id || fresh.version >= versionRef.current)) {
      setA(fresh);
      versionRef.current = fresh.version;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisId, ctx.project]);

  useEffect(() => {
    if (!a) return;
    api.get<Dataset>(`/api/projects/${pid}/datasets/${a.dataset_id}`).then(setDs);
  }, [pid, a?.dataset_id, ctx.project.datasets]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => groupNames(ds?.content, a?.config), [ds, a?.config]);

  if (!a) return null;
  const spec = ctx.types.find((x) => x.kind === a.kind);
  const cfg = a.config;
  const isXY = spec?.input === "xy";
  const pairedDesign: boolean = cfg.paired_design ?? (a.kind === "wilcoxon" || cfg.variant === "paired" || (a.kind === "pairwise" && (cfg.test === "paired" || cfg.test === "wilcoxon")));

  function save(patch: Partial<Analysis>) {
    const next = { ...a!, ...patch } as Analysis;
    setA(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        const r = await api.put<Analysis>(`/api/projects/${pid}/analyses/${a!.id}`, { name: next.name, config: next.config, kind: next.kind, version: versionRef.current });
        versionRef.current = r.version;
        ctx.reload();
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) setErr(t("conflict"));
      }
    }, 400);
  }
  const setCfg = (p: Record<string, unknown>) => save({ config: { ...cfg, ...p } });

  async function changeKind(kind: string) {
    // kind is part of the analysis identity; recreate rather than mutate history
    const r = await api.post<Analysis>(`/api/projects/${pid}/analyses`, { dataset_id: a!.dataset_id, kind, name: a!.name, config: { ...cfg } });
    await api.del(`/api/projects/${pid}/analyses/${a!.id}`);
    await ctx.reload();
    nav(`/p/${pid}/analysis/${r.id}`, { replace: true });
  }

  async function changeDataset(dataset_id: string) {
    const r = await api.post<Analysis>(`/api/projects/${pid}/analyses`, { dataset_id, kind: a!.kind, name: a!.name, config: { ...cfg, groups: undefined, x: undefined, y: undefined } });
    await api.del(`/api/projects/${pid}/analyses/${a!.id}`);
    await ctx.reload();
    nav(`/p/${pid}/analysis/${r.id}`, { replace: true });
  }

  async function run() {
    setBusy(true);
    setErr("");
    window.clearTimeout(timer.current);
    try {
      // make sure the latest config is saved before running
      const saved = await api.put<Analysis>(`/api/projects/${pid}/analyses/${a!.id}`, { name: a!.name, config: a!.config, version: versionRef.current });
      versionRef.current = saved.version;
      const r = await api.post<Analysis>(`/api/projects/${pid}/analyses/${a!.id}/run`);
      countRun(a!.kind);
      versionRef.current = r.version;
      setA(r);
      await ctx.reload();
    } catch (e) {
      setErr(e instanceof ApiError ? JSON.stringify(e.detail) : String(e));
    } finally {
      setBusy(false);
    }
  }

  const sel = (cfg.groups as string[] | undefined) ?? groups.slice(0, 2);
  const numericCols = ds?.content.columns.filter((c) => c.type === "numeric") ?? [];
  const hasSubject = !!ds?.content.design?.subject;
  const compareTypes = ctx.types.filter((x) => x.family === "compare2" || x.family === "compareN" || x.kind === "descriptive");
  const xyTypes = ctx.types.filter((x) => x.family === "xy" || x.family === "survival");

  return (
    <>
      <div className="main-header">
        <input className="name-input" value={a.name} disabled={!canEdit} onChange={(e) => save({ name: e.target.value })} aria-label="name" />
        {a.status === "stale" && <span className="badge stale" title={t("staleExplain")}>{t("stale")}</span>}
        {a.status === "up_to_date" && <span className="badge ok">{t("upToDate")}</span>}
        {a.status === "never_run" && <span className="badge">{t("neverRun")}</span>}
        <div className="grow" />
        {canEdit && <button className="primary" onClick={run} disabled={busy}>{a.last_result ? t("rerun") : t("run")}</button>}
        {canEdit && <button className="small danger" onClick={async () => {
          if (!window.confirm(t("confirmDelete"))) return;
          await api.del(`/api/projects/${pid}/analyses/${a.id}`);
          await ctx.reload();
          nav(`/p/${pid}`);
        }}>{t("delete")}</button>}
      </div>
      {a.status === "stale" && <div className="notice warn">{t("staleExplain")}</div>}
      {err && <div className="notice error">{err}</div>}

      <div className="panel">
        <div className="grid2">
          <label className="field"><span>{t("table")}</span>
            <select value={a.dataset_id} disabled={!canEdit} onChange={(e) => changeDataset(e.target.value)}>
              {ctx.project.datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
          <label className="field"><span>{t("analysisType")}</span>
            <select value={a.kind} disabled={!canEdit} onChange={(e) => changeKind(e.target.value)}>
              <optgroup label={t("groupsToCompare")}>
                {compareTypes.map((x) => <option key={x.kind} value={x.kind} disabled={!x.available}>{L(x.label)}{!x.available ? ` — ${t("comingSoon")}` : ""}</option>)}
              </optgroup>
              <optgroup label="X / Y">
                {xyTypes.map((x) => <option key={x.kind} value={x.kind} disabled={!x.available}>{L(x.label)}{!x.available ? ` — ${t("comingSoon")}` : ""}</option>)}
              </optgroup>
            </select>
          </label>
        </div>

        {!isXY && (
          <>
            <h4 style={{ marginTop: 6 }}>{t("designQuestions")}</h4>
            {a.kind !== "descriptive" && (
              <div className="field"><span>{t("independentOrPaired")}</span>
                <div className="row">
                  <label className="check" style={{ marginBottom: 0 }}><input type="radio" disabled={!canEdit} checked={!pairedDesign} onChange={() => setCfg({ paired_design: false, ...(a.kind === "ttest" ? { variant: "welch" } : {}), ...(a.kind === "pairwise" ? { test: "welch" } : {}) })} /> {t("independent")}</label>
                  <label className="check" style={{ marginBottom: 0 }}><input type="radio" disabled={!canEdit} checked={pairedDesign} onChange={() => setCfg({ paired_design: true, ...(a.kind === "ttest" ? { variant: "paired" } : {}), ...(a.kind === "pairwise" ? { test: "paired" } : {}) })} /> {t("pairedQ")}</label>
                </div>
              </div>
            )}
            {((a.kind === "mannwhitney" && pairedDesign) || (a.kind === "wilcoxon" && !pairedDesign) || (a.kind === "ttest" && pairedDesign !== (cfg.variant === "paired"))) &&
              <div className="notice warn">{t("pairedMismatch")}</div>}
            <div className="grid2">
              <label className="field"><span>{t("expUnit")}</span><input value={cfg.experimental_unit || ""} disabled={!canEdit} onChange={(e) => setCfg({ experimental_unit: e.target.value })} /></label>
              <div>
                <label className="check" style={{ marginTop: 18 }}><input type="checkbox" disabled={!canEdit || !hasSubject} checked={!!cfg.average_technical} onChange={(e) => setCfg({ average_technical: e.target.checked })} /> <span>{t("avgTech")}<br /><span className="small-text muted">{t("avgTechHelp")}</span></span></label>
              </div>
            </div>
            <div className="grid2">
              <label className="field"><span>{t("groupColPick")}</span>
                <select value={cfg.group_col ?? ""} disabled={!canEdit} onChange={(e) => setCfg({ group_col: e.target.value || null, groups: undefined })}>
                  {!cfg.value_col && <option value="">{t("useTableDesign")}</option>}
                  {cfg.value_col && <option value="">{t("none")}</option>}
                  {factorColumns(ds?.content).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select></label>
              <label className="field"><span>{t("valueColPick")}</span>
                <div className="row tight" style={{ flexWrap: "nowrap" }}>
                  <select className="grow" value={cfg.value_col ?? ""} disabled={!canEdit} onChange={(e) => setCfg({ value_col: e.target.value || null, ...(e.target.value ? {} : { group_col: null }), groups: cfg.group_col ? cfg.groups : undefined })}>
                    <option value="">{t("useTableDesign")}</option>
                    {numericColumns(ds?.content).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                  {cfg.value_col && <StepButtons cols={numericColumns(ds?.content).map((c) => c.id)} value={cfg.value_col} disabled={!canEdit} onChange={(v) => setCfg({ value_col: v })} />}
                </div></label>
            </div>
            <div className="field"><span>{t("groupsToCompare")}</span>
              {a.kind === "descriptive" || a.kind === "pairwise" ? (
                <div className="row">{groups.map((g) => (
                  <label key={g} className="check" style={{ marginBottom: 0 }}><input type="checkbox" disabled={!canEdit} checked={!cfg.groups || cfg.groups.includes(g)}
                    onChange={(e) => { const cur: string[] = cfg.groups ?? groups; setCfg({ groups: e.target.checked ? [...cur, g] : cur.filter((x) => x !== g) }); }} /> {g}</label>
                ))}</div>
              ) : (
                <div className="row">
                  {[0, 1].map((i) => (
                    <select key={i} value={sel[i] ?? ""} disabled={!canEdit} onChange={(e) => { const next = [...sel]; next[i] = e.target.value; setCfg({ groups: next }); }} aria-label={`${t("group")} ${i + 1}`}>
                      {groups.map((g) => <option key={g} value={g}>{g}</option>)}
                    </select>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
        {isXY && (
          <div className="grid3">
            <label className="field"><span>{t("xColumn")}</span>
              <select value={cfg.x || ""} disabled={!canEdit} onChange={(e) => setCfg({ x: e.target.value })}><option value="">—</option>{numericCols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
            <label className="field"><span>{t("yColumn")}</span>
              <select value={cfg.y || ""} disabled={!canEdit} onChange={(e) => setCfg({ y: e.target.value })}><option value="">—</option>{numericCols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
            {a.kind === "correlation" && <label className="field"><span>{t("method")}</span>
              <select value={cfg.method || "pearson"} disabled={!canEdit} onChange={(e) => setCfg({ method: e.target.value })}><option value="pearson">Pearson</option><option value="spearman">Spearman</option></select></label>}
          </div>
        )}
        {a.kind === "pairwise" && (
          <div className="grid2">
            <label className="field"><span>{lang === "fr" ? "Comparer" : "Compare"}</span>
              <select value={cfg.mode || "all"} disabled={!canEdit} onChange={(e) => setCfg({ mode: e.target.value })}>
                <option value="all">{lang === "fr" ? "Toutes les paires" : "All pairs"}</option>
                <option value="control">{lang === "fr" ? "Chaque groupe vs contrôle" : "Each group vs control"}</option></select></label>
            {(cfg.mode || "all") === "control" ? <label className="field"><span>{lang === "fr" ? "Contrôle" : "Control"}</span>
              <select value={cfg.control || groups[0] || ""} disabled={!canEdit} onChange={(e) => setCfg({ control: e.target.value })}>
                {groups.map((g) => <option key={g}>{g}</option>)}</select></label> : <div />}
            <label className="field"><span>{t("testUsed")}</span>
              <select value={cfg.test || "welch"} disabled={!canEdit} onChange={(e) => setCfg({ test: e.target.value, paired_design: e.target.value === "paired" || e.target.value === "wilcoxon" })}>
                <option value="welch">{lang === "fr" ? "t de Welch (non apparié)" : "Welch t (unpaired)"}</option>
                <option value="student">{lang === "fr" ? "t de Student" : "Student t"}</option>
                <option value="mannwhitney">Mann–Whitney</option>
                <option value="paired">{lang === "fr" ? "t apparié" : "Paired t"}</option>
                <option value="wilcoxon">{lang === "fr" ? "Wilcoxon apparié" : "Wilcoxon paired"}</option></select></label>
            <label className="field"><span>{lang === "fr" ? "Correction multiple" : "Multiple-comparison correction"}</span>
              <select value={cfg.correction || "holm"} disabled={!canEdit} onChange={(e) => setCfg({ correction: e.target.value })}>
                <option value="holm">Holm–Bonferroni</option><option value="bonferroni">Bonferroni</option>
                <option value="none">{lang === "fr" ? "Aucune" : "None"}</option></select></label>
          </div>
        )}
        <div className="grid3">
          {a.kind === "ttest" && <label className="field"><span>{t("variant")}</span>
            <select value={cfg.variant || "welch"} disabled={!canEdit} onChange={(e) => setCfg({ variant: e.target.value, paired_design: e.target.value === "paired" })}>
              <option value="welch">{t("welch")}</option><option value="student">{t("student")}</option><option value="paired">{t("paired")}</option></select></label>}
          {a.kind !== "descriptive" && a.kind !== "linear_regression" && <label className="field"><span>{t("alternative")}</span>
            <select value={cfg.alternative || "two-sided"} disabled={!canEdit} onChange={(e) => setCfg({ alternative: e.target.value })}>
              <option value="two-sided">{t("twoSided")}</option><option value="less">{t("less")}</option><option value="greater">{t("greater")}</option></select></label>}
          <label className="field"><span>{t("ciLevel")}</span>
            <select value={String(cfg.ci_level ?? 0.95)} disabled={!canEdit} onChange={(e) => setCfg({ ci_level: Number(e.target.value) })}>
              <option value="0.9">90 %</option><option value="0.95">95 %</option><option value="0.99">99 %</option></select></label>
        </div>
      </div>

      {a.last_result && <ResultView result={a.last_result.result} meta={a.last_result} stale={a.status === "stale"} />}
    </>
  );
}

export function StepButtons({ cols, value, onChange, disabled }: { cols: string[]; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const i = cols.indexOf(value);
  return (
    <>
      <button type="button" className="small" disabled={disabled || i <= 0} aria-label="previous" onClick={() => onChange(cols[i - 1])}>◀</button>
      <button type="button" className="small" disabled={disabled || i < 0 || i >= cols.length - 1} aria-label="next" onClick={() => onChange(cols[i + 1])}>▶</button>
    </>
  );
}
