import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, Dataset, FigureEntity } from "../api";
import { Key, useI18n } from "../i18n";
import type { ProjectCtx } from "../pages/ProjectPage";
import { groupNames, StepButtons } from "./AnalysisView";
import { factorColumns, levels, numericColumns } from "../columns";
import FigureCanvas, { Selection } from "./FigureCanvas";
import RichInput from "./RichText";
import StyleMenu from "./StyleMenu";
import Inspector, { figurePFormat, PFormat, StarThresholds } from "./Inspector";
import type { Analysis } from "../api";

// Quick styles. "Classic" and "Poster" scale text, lines and points with the figure size (reference 80 × 70 mm)
// so that a style looks right on a small panel as on a full-width figure; journal styles keep absolute sizes.
function scaled(w: number, h: number, cap: number, v: { font: number; title: number; lw: number; pt: number; tick: number }) {
  const k = Math.min(cap, Math.max(0.8, Math.sqrt((w * h) / 5600)));
  const r = (x: number, q = 0.5) => Math.round((x * k) / q) * q;
  return { font_size_pt: r(v.font), title_size_pt: r(v.title), line_width_pt: r(v.lw, 0.25), point_size_pt: r(v.pt), tick_length_pt: r(v.tick) };
}
const PRESETS: Record<string, { label: [string, string]; cfg: (w: number, h: number) => Record<string, unknown> }> = {
  prism: { label: ["Classique (type Prism)", "Classic (Prism-like)"], cfg: (w, h) => ({ font_family: "Arial", tick_direction: "out", frame: "open", minor_ticks: 0, palette: null, fill_opacity: 0.3, ...scaled(w, h, 1.3, { font: 10, title: 12, lw: 1, pt: 5, tick: 4 }) }) },
  journal: { label: ["Revue (Nature / Cell)", "Journal (Nature / Cell)"], cfg: () => ({ font_family: "Arial", font_size_pt: 7, title_size_pt: 8, line_width_pt: 0.5, tick_direction: "out", tick_length_pt: 2, frame: "open", point_size_pt: 3, minor_ticks: 0, palette: null, fill_opacity: 0.3 }) },
  boxed: { label: ["Encadré, graduations internes", "Boxed, inside ticks"], cfg: () => ({ frame: "closed", tick_direction: "in", tick_length_pt: 3, minor_ticks: 1, line_width_pt: 0.75 }) },
  poster: { label: ["Poster / présentation", "Poster / slides"], cfg: (w, h) => ({ frame: "open", tick_direction: "out", minor_ticks: 0, ...scaled(w, h, 1.6, { font: 10, title: 12, lw: 1.5, pt: 6, tick: 4 }) }) },
  minimal: { label: ["Minimal", "Minimal"], cfg: () => ({ frame: "open", tick_direction: "out", tick_length_pt: 2, minor_ticks: 0, line_width_pt: 0.5, fill_opacity: 0.15, palette: "grayscale" }) },
};

const PALETTES: Record<string, string[]> = {
  okabe_ito: ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#56B4E9", "#CC79A7", "#F0E442", "#000000"],
  tol_bright: ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB"],
  tol_muted: ["#332288", "#88CCEE", "#44AA99", "#117733", "#999933", "#DDCC77", "#CC6677", "#882255", "#AA4499"],
  grayscale: ["#1A1A1A", "#737373", "#B3B3B3", "#404040", "#999999", "#D9D9D9"],
};
const Y_KEYS = ["y_min", "y_max", "y_tick_step", "y_scale", "y_label", "y_unit"];
const GROUP_TYPES = ["scatter_groups", "bar", "box", "violin"];
const XY_TYPES = ["xy", "line"];

type Cfg = Record<string, any>;

export default function FigureView({ ctx }: { ctx: ProjectCtx }) {
  const { figureId } = useParams();
  const { t, L, lang } = useI18n();
  const nav = useNavigate();
  const pid = ctx.project.id;
  const canEdit = ctx.project.permissions.includes("edit");
  const initial = ctx.project.figures.find((f) => f.id === figureId);
  const [fig, setFig] = useState<FigureEntity | null>(initial ?? null);
  const [cfg, setCfgState] = useState<Cfg>(initial?.config ?? {});
  const [ds, setDs] = useState<Dataset | null>(null);
  const [svg, setSvg] = useState("");
  const [caption, setCaption] = useState("");
  const [warnings, setWarnings] = useState<{ en: string; fr: string }[]>([]);
  const [renderErr, setRenderErr] = useState("");
  const [saveErr, setSaveErr] = useState("");
  const renderSeq = useRef(0);
  const saveChain = useRef<Promise<void>>(Promise.resolve());
  const pendingSave = useRef<{ cfg: Cfg; extra: Partial<{ name: string; dataset_id: string }> } | null>(null);
  const [saved, setSaved] = useState(true);
  const [zoom, setZoom] = useState(1.5);
  const [dpi, setDpi] = useState(300);
  const [bg, setBg] = useState<"white" | "transparent">("white");
  const [selected, setSelected] = useState<Selection | null>(null);
  const [starsBusy, setStarsBusy] = useState(false);
  const [starsOpts, setStarsOpts] = useState<{ mode: string; control: string; test: string; correction: string }>({ mode: "all", control: "", test: "welch", correction: "holm" });
  const undoStack = useRef<Cfg[]>([]);
  const redoStack = useRef<Cfg[]>([]);
  const lastPush = useRef(0);
  const [, force] = useState(0);
  const versionRef = useRef(initial?.version ?? 0);
  const saveTimer = useRef<number>();
  const undoRef = useRef<{ undo: () => void; redo: () => void }>({ undo: () => {}, redo: () => {} });
  const renderTimer = useRef<number>();

  useEffect(() => {
    const f = ctx.project.figures.find((x) => x.id === figureId) ?? null;
    if (f && (!fig || f.id !== fig.id)) {
      setFig(f);
      setCfgState(f.config);
      versionRef.current = f.version;
    } else if (f) {
      setFig((old) => (old ? { ...old, stale: f.stale } : f));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [figureId, ctx.project]);

  useEffect(() => {
    if (!fig) return;
    api.get<Dataset>(`/api/projects/${pid}/datasets/${fig.dataset_id}`).then(setDs);
  }, [pid, fig?.dataset_id, ctx.project.datasets]); // eslint-disable-line react-hooks/exhaustive-deps

  // live preview (same renderer as exports)
  useEffect(() => {
    if (!fig) return;
    window.clearTimeout(renderTimer.current);
    renderTimer.current = window.setTimeout(async () => {
      const seq = ++renderSeq.current;
      try {
        const r = await api.post<{ svg: string; caption: string; warnings: { en: string; fr: string }[] }>(`/api/projects/${pid}/render`, { dataset_id: fig.dataset_id, config: { ...cfg, background: bg }, lang });
        if (seq !== renderSeq.current) return; // a newer render is on its way: never show an outdated figure
        setSvg(r.svg);
        setCaption(r.caption);
        setWarnings(r.warnings);
        setRenderErr("");
      } catch (e) {
        if (seq !== renderSeq.current) return;
        setRenderErr(e instanceof ApiError ? (e.detail?.message ? L(e.detail.message) : JSON.stringify(e.detail)) : String(e));
      }
    }, 200);
  }, [cfg, fig?.dataset_id, pid, lang, bg, ctx.project.analyses, ds]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    const c = ds?.content;
    if (!c) return [];
    const ty = cfg.type ?? "scatter_groups";
    if (GROUP_TYPES.includes(ty)) return groupNames(c, cfg);
    const ys: string[] = Array.isArray(cfg.y) ? cfg.y : cfg.y ? [cfg.y] : [];
    const names = (id: string) => c.columns.find((x) => x.id === id)?.name ?? id;
    if (cfg.color_by) return ys.length <= 1 ? levels(c, cfg.color_by) : ys.flatMap((y) => levels(c, cfg.color_by).map((l) => `${names(y)} – ${l}`));
    return ys.map(names);
  }, [ds, cfg]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (e.target as HTMLElement).isContentEditable) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) undoRef.current.redo(); else undoRef.current.undo(); }
      else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "y") { e.preventDefault(); undoRef.current.redo(); }
      else if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  if (!fig) return null;

  // Saves are debounced and strictly sequential: a save never starts while the previous one is in
  // flight, so our own edits can never collide with each other on the optimistic version number.
  async function flushSave() {
    const job = pendingSave.current;
    if (!job) return;
    pendingSave.current = null;
    try {
      const r = await api.put<{ version: number }>(`/api/projects/${pid}/figures/${fig!.id}`, { config: job.cfg, version: versionRef.current, ...job.extra });
      versionRef.current = r.version;
      setSaveErr("");
      if (!pendingSave.current) setSaved(true);
      if (job.extra.dataset_id || job.extra.name) ctx.reload();
    } catch (e) {
      setSaveErr(e instanceof ApiError && e.status === 409 ? t("conflict") : (lang === "fr" ? "Échec de l’enregistrement. Vos modifications sont conservées dans cette page ; réessayez." : "Saving failed. Your changes are kept on this page; try again."));
      if (!pendingSave.current) pendingSave.current = job;
    }
  }
  async function retrySave() {
    setSaveErr("");
    try {
      const p = await api.get<{ figures: { id: string; version: number }[] }>(`/api/projects/${pid}`);
      const f = p.figures.find((x) => x.id === fig!.id);
      if (f) versionRef.current = f.version;
    } catch { /* keep the known version */ }
    if (!pendingSave.current) pendingSave.current = { cfg, extra: {} };
    saveChain.current = saveChain.current.then(flushSave);
  }
  function persist(next: Cfg, extra: Partial<{ name: string; dataset_id: string }> = {}) {
    setSaved(false);
    pendingSave.current = { cfg: next, extra: { ...(pendingSave.current?.extra ?? {}), ...extra } };
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => { saveChain.current = saveChain.current.then(flushSave); }, 500);
  }
  const set = (p: Cfg) => {
    const next = { ...cfg, ...p };
    // group rapid edits (typing, sliders) into one undo step
    if (Date.now() - lastPush.current > 700) {
      undoStack.current.push(cfg);
      if (undoStack.current.length > 100) undoStack.current.shift();
      redoStack.current = [];
    }
    lastPush.current = Date.now();
    setCfgState(next);
    persist(next);
  };
  const undo = () => {
    const prev = undoStack.current.pop();
    if (!prev) return;
    redoStack.current.push(cfg);
    lastPush.current = 0;
    setCfgState(prev);
    persist(prev);
    force((n) => n + 1);
  };
  const redo = () => {
    const nxt = redoStack.current.pop();
    if (!nxt) return;
    undoStack.current.push(cfg);
    lastPush.current = 0;
    setCfgState(nxt);
    persist(nxt);
    force((n) => n + 1);
  };
  undoRef.current = { undo, redo };

  const Wpt = ((cfg.width_mm ?? 80) * 72) / 25.4;
  const Hpt = ((cfg.height_mm ?? 70) * 72) / 25.4;
  function onDrag(s: Selection, dx: number, dy: number) {
    if (s.el === "title") set({ title_dx: (cfg.title_dx ?? 0) + dx, title_dy: (cfg.title_dy ?? 0) + dy });
    else if (s.el === "legend") set({ legend_dx: (cfg.legend_dx ?? 0) + dx, legend_dy: (cfg.legend_dy ?? 0) + dy });
    else if (s.el === "annotation" && s.index !== undefined) {
      const n = [...(cfg.annotations ?? [])];
      const a = n[s.index];
      n[s.index] = { ...a, x: Math.min(1, Math.max(0, (a.x ?? 0.5) + dx / Wpt)), y: Math.min(1, Math.max(0, (a.y ?? 0.1) + dy / Hpt)) };
      set({ annotations: n });
    }
  }
  function onInlineEdit(s: Selection) {
    const autoName = (id?: string) => ds?.content.columns.find((c) => c.id === id)?.name ?? "";
    switch (s.el) {
      case "title": return { value: cfg.title ?? "", commit: (v: string) => set({ title: v }) };
      case "x-label": return { value: cfg.x_label || autoName(isGroup ? cfg.group_col : cfg.x), commit: (v: string) => set({ x_label: v }) };
      case "y-label": return { value: cfg.y_label || autoName(isGroup ? cfg.value_col : (cfg.y ?? [])[0]), commit: (v: string) => set({ y_label: v }) };
      case "group-label": return { value: (cfg.group_labels ?? {})[s.group!] ?? s.group!, commit: (v: string) => set({ group_labels: { ...(cfg.group_labels ?? {}), [s.group!]: v } }) };
      case "annotation": {
        const k = s.index!;
        return { value: (cfg.annotations ?? [])[k]?.text ?? "", commit: (v: string) => { const n = [...(cfg.annotations ?? [])]; n[k] = { ...n[k], text: v }; set({ annotations: n }); } };
      }
    }
    return null;
  }
  function rowLabel(rowId: string) {
    const rows = ds?.content.rows ?? [];
    const i = rows.findIndex((r) => r.id === rowId);
    return i < 0 ? "" : `${lang === "fr" ? "Ligne" : "Row"} ${i + 1}`;
  }
  async function excludeRow(rowId: string) {
    const reason = window.prompt(t("excludeReason"));
    if (!reason || !reason.trim()) return;
    const d = await api.get<Dataset>(`/api/projects/${pid}/datasets/${fig!.dataset_id}`);
    const content = { ...d.content, exclusions: [...d.content.exclusions.filter((e) => e.row_id !== rowId), { row_id: rowId, reason: reason.trim(), at: new Date().toISOString() }] };
    await api.put(`/api/projects/${pid}/datasets/${d.id}`, { content, version: d.version, change_note: `exclude ${rowLabel(rowId)} from figure` });
    setSelected(null);
    await ctx.reload();
  }
  // the stars panel shows the settings actually used by the saved stars analysis
  const starsAnalysisId = ((cfg.annotations ?? []) as Cfg[]).find((a) => a.type === "pvalue" && a.all)?.analysis_id as string | undefined;
  useEffect(() => {
    const an = starsAnalysisId ? ctx.project.analyses.find((x) => x.id === starsAnalysisId) : undefined;
    if (!an) return;
    const c = (an.config ?? {}) as Record<string, any>;
    setStarsOpts({ mode: c.mode ?? "all", control: c.control ?? "", test: c.test ?? "welch", correction: c.correction ?? "holm" });
  }, [starsAnalysisId, ctx.project.analyses]);

  // Axis scaling and significance stars belong to one Y variable. Switching Y saves them for the variable
  // being left and restores those of the new one (or lets the axis autoscale if it was never adjusted).
  function yKey(c: Cfg): string {
    return GROUP_TYPES.includes(c.type ?? "scatter_groups") ? String(c.value_col ?? "") : (Array.isArray(c.y) ? c.y.join("+") : "");
  }
  function switchY(patch: Cfg) {
    const oldK = yKey(cfg);
    const newK = yKey({ ...cfg, ...patch });
    if (oldK === newK) return set(patch);
    const anns: Cfg[] = cfg.annotations ?? [];
    const per: Record<string, Cfg> = { ...(cfg.per_y ?? {}) };
    per[oldK] = { ...Object.fromEntries(Y_KEYS.map((k) => [k, cfg[k] ?? null])), annotations: anns.filter((a) => a.type === "pvalue") };
    const saved = per[newK];
    set({
      ...patch,
      ...Object.fromEntries(Y_KEYS.map((k) => [k, saved ? saved[k] ?? null : null])),
      per_y: per,
      annotations: [...anns.filter((a) => a.type !== "pvalue"), ...((saved?.annotations as Cfg[]) ?? [])],
    });
  }

  async function autoStars() {
    setStarsBusy(true);
    try {
      const existing = annotations.findIndex((a) => a.type === "pvalue" && a.all);
      const config = {
        group_col: cfg.group_col ?? null, value_col: cfg.value_col ?? null, average_technical: !!cfg.average_technical,
        groups: groups.length ? order : undefined, mode: starsOpts.mode, control: starsOpts.control || order[0], test: starsOpts.test, correction: starsOpts.correction,
      };
      let aid: string;
      const old = existing >= 0 ? ctx.project.analyses.find((x) => x.id === annotations[existing].analysis_id) : undefined;
      if (old) {
        const u = await api.put<Analysis>(`/api/projects/${pid}/analyses/${old.id}`, { config, version: old.version });
        aid = u.id;
      } else {
        const a = await api.post<Analysis>(`/api/projects/${pid}/analyses`, { dataset_id: fig!.dataset_id, kind: "pairwise", name: `${lang === "fr" ? "Étoiles" : "Stars"} – ${fig!.name}${cfg.value_col ? ` – ${ds?.content.columns.find((c) => c.id === cfg.value_col)?.name ?? ""}` : ""}`, config });
        aid = a.id;
      }
      await api.post(`/api/projects/${pid}/analyses/${aid}/run`);
      const ann = { type: "pvalue", analysis_id: aid, all: true };
      // the automatic set replaces any earlier bracket from the same analysis: one bracket per pair
      const others = annotations.filter((a) => !(a.type === "pvalue" && (a.all || a.analysis_id === aid)));
      set({ annotations: [...others, ann], p_format: figurePFormat(cfg) });
      await ctx.reload();
    } catch (e) {
      setRenderErr(e instanceof ApiError ? JSON.stringify(e.detail) : String(e));
    } finally {
      setStarsBusy(false);
    }
  }

  const type: string = cfg.type ?? "scatter_groups";
  const isGroup = GROUP_TYPES.includes(type);
  const numericCols = numericColumns(ds?.content);
  const factorCols = factorColumns(ds?.content);
  const order: string[] = (cfg.group_order as string[] | undefined)?.filter((g) => groups.includes(g)).concat(groups.filter((g) => !(cfg.group_order || []).includes(g))) ?? groups;
  const palette = PALETTES[cfg.palette ?? "okabe_ito"];
  const w = cfg.width_mm ?? 80;
  const h = cfg.height_mm ?? 70;
  const pngPx = (mm: number) => Math.round((mm / 25.4) * dpi);
  const pAnalyses = ctx.project.analyses.filter((a) => a.dataset_id === fig.dataset_id && a.last_result?.result.status === "ok" && (a.last_result.result.comparisons?.length ?? 0) > 0);
  const annotations: Cfg[] = cfg.annotations ?? [];
  const exportUrl = (fmt: string) => `/api/projects/${pid}/figures/${fig.id}/export?format=${fmt}&dpi=${dpi}&background=${bg}&lang=${lang}`;
  const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));
  const field = (k: Key, key: string, step = 0.25) => (
    <label className="field"><span>{t(k)}</span><input type="number" step={step} value={cfg[key] ?? ""} disabled={!canEdit} onChange={(e) => set({ [key]: num(e.target.value) })} /></label>
  );

  return (
    <>
      <div className="main-header">
        <input className="name-input" value={fig.name} disabled={!canEdit} onChange={(e) => { setFig({ ...fig, name: e.target.value }); persist(cfg, { name: e.target.value }); }} aria-label="name" />
        {fig.stale && <span className="badge stale" title={t("staleExplain")}>{t("stale")}</span>}
        <span className="small-text muted">{saved ? t("saved") : t("saving")}</span>
        <div className="grow" />
        {canEdit && <>
          <button className="small" onClick={undo} disabled={!undoStack.current.length} title="Ctrl/Cmd+Z">↶ {lang === "fr" ? "Annuler" : "Undo"}</button>
          <button className="small" onClick={redo} disabled={!redoStack.current.length} title="Ctrl/Cmd+Shift+Z">↷ {lang === "fr" ? "Rétablir" : "Redo"}</button>
          <StyleMenu cfg={cfg} set={set} canEdit={canEdit} builtins={Object.entries(PRESETS).map(([k, p]) => [k, p.label[lang === "fr" ? 0 : 1]])}
            applyBuiltin={(k) => set(PRESETS[k].cfg(Number(cfg.width_mm) || 80, Number(cfg.height_mm) || 70))} />
        </>}
        {canEdit && <button className="small danger" onClick={async () => {
          if (!window.confirm(t("confirmDelete"))) return;
          await api.del(`/api/projects/${pid}/figures/${fig.id}`);
          await ctx.reload();
          nav(`/p/${pid}`);
        }}>{t("delete")}</button>}
      </div>
      <div className="fig-layout">
        <div className="fig-controls">
          <details open>
            <summary>{t("figureType")}</summary>
            <div>
              <label className="field"><span>{t("table")}</span>
                <select value={fig.dataset_id} disabled={!canEdit} onChange={(e) => { setFig({ ...fig, dataset_id: e.target.value }); persist(cfg, { dataset_id: e.target.value }); }}>
                  {ctx.project.datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select></label>
              <label className="field"><span>{t("figureType")}</span>
                <select value={type} disabled={!canEdit} onChange={(e) => set({ type: e.target.value })}>
                  {[...GROUP_TYPES, ...XY_TYPES].map((k) => <option key={k} value={k}>{t(k as Key)}</option>)}
                  <option disabled>{t("doseResponse")} — {t("comingSoon")}</option>
                </select></label>
              {isGroup && (
                <>
                  <label className="field"><span>{t("xAxisGroups")}</span>
                    <select value={cfg.group_col ?? ""} disabled={!canEdit} onChange={(e) => set({ group_col: e.target.value || null, group_order: null })}>
                      {cfg.value_col ? <option value="">{t("none")}</option> : <option value="">{t("useTableDesign")}</option>}
                      {factorCols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select></label>
                  <label className="field"><span>{t("valueColPick")}</span>
                    <div className="row tight" style={{ flexWrap: "nowrap" }}>
                      <select className="grow" value={cfg.value_col ?? ""} disabled={!canEdit} onChange={(e) => switchY({ value_col: e.target.value || null, ...(e.target.value ? {} : { group_col: null }) })}>
                        <option value="">{t("useTableDesign")}</option>
                        {numericCols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                      </select>
                      {cfg.value_col && <StepButtons cols={numericCols.map((c) => c.id)} value={cfg.value_col} disabled={!canEdit} onChange={(v) => switchY({ value_col: v })} />}
                    </div></label>
                </>
              )}
              {!isGroup && (
                <>
                  <label className="field"><span>{t("colorBy")}</span>
                    <select value={cfg.color_by ?? ""} disabled={!canEdit} onChange={(e) => set({ color_by: e.target.value || null, group_order: null })}>
                      <option value="">{t("none")}</option>
                      {factorCols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select></label>
                  {Array.isArray(cfg.y) && cfg.y.length === 1 && (
                    <div className="row tight" style={{ marginBottom: 8 }}>
                      <span className="small-text muted grow">Y : {numericCols.find((c) => c.id === cfg.y[0])?.name}</span>
                      <StepButtons cols={numericCols.filter((c) => c.id !== cfg.x).map((c) => c.id)} value={cfg.y[0]} disabled={!canEdit} onChange={(v) => switchY({ y: [v] })} />
                    </div>
                  )}
                  <label className="field"><span>{t("xColumn")}</span>
                    <select value={cfg.x ?? ""} disabled={!canEdit} onChange={(e) => set({ x: e.target.value })}><option value="">—</option>{numericCols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
                  <div className="field"><span>{t("yColumns")}</span>
                    {numericCols.filter((c) => c.id !== cfg.x).map((c) => (
                      <label key={c.id} className="check" style={{ marginBottom: 2 }}><input type="checkbox" disabled={!canEdit} checked={(cfg.y ?? []).includes(c.id)}
                        onChange={(e) => set({ y: e.target.checked ? [...(cfg.y ?? []), c.id] : (cfg.y ?? []).filter((x: string) => x !== c.id) })} /> {c.name}</label>
                    ))}
                  </div>
                </>
              )}
            </div>
          </details>
          <details open>
            <summary>{t("size")}</summary>
            <div className="grid2">
              <label className="field"><span>{t("width")} (mm)</span><input type="number" min={20} max={500} value={w} disabled={!canEdit} onChange={(e) => set({ width_mm: Number(e.target.value) })} /></label>
              <label className="field"><span>{t("height")} (mm)</span><input type="number" min={20} max={500} value={h} disabled={!canEdit} onChange={(e) => set({ height_mm: Number(e.target.value) })} /></label>
              <span className="small-text muted" style={{ gridColumn: "1 / 3" }}>= {(w / 10).toLocaleString(lang)} × {(h / 10).toLocaleString(lang)} cm ·{" "}
                {[["85", t("width") + " 1 col."], ["180", "2 col."]].map(([v, l]) => <button key={v} className="ghost small" disabled={!canEdit} onClick={() => set({ width_mm: Number(v) })}>{l} ({v} mm)</button>)}</span>
            </div>
          </details>
          <details open>
            <summary>{t("titles")}</summary>
            <div>
              <div className="field"><span>{t("title")}</span><RichInput ariaLabel={t("title")} value={cfg.title ?? ""} disabled={!canEdit} onChange={(v) => set({ title: v })} /></div>
              <div className="grid2">
                <div className="field"><span>{t("yLabel")}</span><RichInput ariaLabel={t("yLabel")} value={cfg.y_label ?? ""} disabled={!canEdit} onChange={(v) => set({ y_label: v })} /></div>
                <label className="field"><span>{t("unit")} Y</span><input value={cfg.y_unit ?? ""} disabled={!canEdit} onChange={(e) => set({ y_unit: e.target.value })} /></label>
                <div className="field"><span>{t("xLabel")}</span><RichInput ariaLabel={t("xLabel")} value={cfg.x_label ?? ""} disabled={!canEdit} onChange={(v) => set({ x_label: v })} /></div>
                {!isGroup && <label className="field"><span>{t("unit")} X</span><input value={cfg.x_unit ?? ""} disabled={!canEdit} onChange={(e) => set({ x_unit: e.target.value })} /></label>}
              </div>
            </div>
          </details>
          {(isGroup || groups.length > 1) && (
            <details open>
              <summary>{t("groupsOrder")}</summary>
              <div>
                {order.map((g, i) => (
                  <div key={g} className="group-row">
                    <input type="color" className="swatch" aria-label={`color ${g}`} disabled={!canEdit} value={(cfg.colors ?? {})[g] ?? palette[i % palette.length]}
                      onChange={(e) => set({ colors: { ...(cfg.colors ?? {}), [g]: e.target.value } })} />
                    <RichInput value={(cfg.group_labels ?? {})[g] ?? g} disabled={!canEdit} ariaLabel={`label ${g}`} onChange={(v) => set({ group_labels: { ...(cfg.group_labels ?? {}), [g]: v } })} />
                    <button className="ghost small" disabled={!canEdit || i === 0} aria-label="up" onClick={() => { const o = [...order]; [o[i - 1], o[i]] = [o[i], o[i - 1]]; set({ group_order: o }); }}>↑</button>
                    <button className="ghost small" disabled={!canEdit || i === order.length - 1} aria-label="down" onClick={() => { const o = [...order]; [o[i + 1], o[i]] = [o[i], o[i + 1]]; set({ group_order: o }); }}>↓</button>
                  </div>
                ))}
                {cfg.colors && Object.keys(cfg.colors).length > 0 && <button className="small" onClick={() => set({ colors: {} })}>↺ {t("palette")}</button>}
                {isGroup && ds?.content.design?.subject && <label className="check" style={{ marginTop: 8 }}><input type="checkbox" disabled={!canEdit} checked={!!cfg.average_technical} onChange={(e) => set({ average_technical: e.target.checked })} /> {t("avgTech")}</label>}
              </div>
            </details>
          )}
          <details open>
            <summary>{t("summary")}</summary>
            <div>
              {type !== "box" && type !== "violin" && type !== "xy" && (
                <div className="grid2">
                  <label className="field"><span>{t("center")}</span>
                    <select value={cfg.center ?? "mean"} disabled={!canEdit} onChange={(e) => {
                      const c = e.target.value;
                      const err = cfg.error ?? "sd";
                      set({ center: c, error: c === "median" && ["sd", "sem", "ci95"].includes(err) ? "iqr" : c === "mean" && ["iqr"].includes(err) ? "sd" : err });
                    }}>
                      <option value="mean">{t("mean")}</option><option value="median">{t("median")}</option></select></label>
                  <label className="field"><span>{t("errorBars")}</span>
                    <select value={cfg.error ?? "sd"} disabled={!canEdit} onChange={(e) => set({ error: e.target.value })}>
                      {(cfg.center ?? "mean") === "mean"
                        ? <><option value="sd">{t("sd")}</option><option value="sem">{t("sem")}</option><option value="ci95">{t("ci95")}</option></>
                        : <><option value="iqr">{t("iqr")}</option></>}
                      <option value="range">{t("range")}</option><option value="none">{t("noError")}</option>
                    </select></label>
                </div>
              )}
              {type !== "scatter_groups" && type !== "xy" && <label className="check"><input type="checkbox" disabled={!canEdit} checked={cfg.show_points ?? true} onChange={(e) => set({ show_points: e.target.checked })} /> {t("showPoints")}</label>}
              {isGroup && <label className="field"><span>{t("jitter")}</span><input type="range" min={0} max={1} step={0.05} value={cfg.jitter ?? 0.5} disabled={!canEdit} onChange={(e) => set({ jitter: Number(e.target.value) })} /></label>}
              {type === "xy" && <label className="check"><input type="checkbox" disabled={!canEdit} checked={cfg.regression_line ?? true} onChange={(e) => set({ regression_line: e.target.checked })} /> {t("regressionLine")}</label>}
            </div>
          </details>
          <details>
            <summary>{t("axes")}</summary>
            <div>
              <div className="grid2">
                <label className="field"><span>{t("yScale")}</span>
                  <select value={cfg.y_scale ?? "linear"} disabled={!canEdit} onChange={(e) => set({ y_scale: e.target.value })}><option value="linear">{t("scaleLinear")}</option><option value="log10">{t("scaleLog")}</option></select></label>
                <div />
                {field("yMin", "y_min")}{field("yMax", "y_max")}
                {!isGroup && <>
                  <label className="field"><span>{t("xScale")}</span>
                    <select value={cfg.x_scale ?? "linear"} disabled={!canEdit} onChange={(e) => set({ x_scale: e.target.value })}><option value="linear">{t("scaleLinear")}</option><option value="log10">{t("scaleLog")}</option></select></label>
                  <div />
                  {field("xMin", "x_min")}{field("xMax", "x_max")}
                </>}
                <label className="field"><span>{lang === "fr" ? "Cadre" : "Frame"}</span>
                  <select value={cfg.frame ?? "open"} disabled={!canEdit} onChange={(e) => set({ frame: e.target.value })}>
                    <option value="open">└ {lang === "fr" ? "Ouvert (axes X et Y)" : "Open (X and Y axes)"}</option>
                    <option value="closed">▢ {lang === "fr" ? "Fermé (4 côtés)" : "Closed (4 sides)"}</option></select></label>
                <label className="field"><span>{lang === "fr" ? "Graduations" : "Tick marks"}</span>
                  <select value={cfg.tick_direction ?? "out"} disabled={!canEdit} onChange={(e) => set({ tick_direction: e.target.value })}>
                    <option value="out">{lang === "fr" ? "Extérieur" : "Outside"}</option><option value="in">{lang === "fr" ? "Intérieur" : "Inside"}</option><option value="none">{lang === "fr" ? "Aucune" : "None"}</option></select></label>
                {field("lineWidth", "line_width_pt")}
                <label className="field"><span>{lang === "fr" ? "Longueur graduations (pt)" : "Tick length (pt)"}</span>
                  <input type="number" step={0.5} min={0} value={cfg.tick_length_pt ?? 3} disabled={!canEdit} onChange={(e) => set({ tick_length_pt: num(e.target.value) })} /></label>
                <label className="field"><span>{lang === "fr" ? "Graduations mineures" : "Minor ticks"}</span>
                  <select value={cfg.minor_ticks ?? 0} disabled={!canEdit} onChange={(e) => set({ minor_ticks: Number(e.target.value) })}>
                    {[0, 1, 2, 3, 4, 9].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
                <label className="field"><span>{lang === "fr" ? "Pas Y" : "Y step"}</span>
                  <input type="number" step="any" value={cfg.y_tick_step ?? ""} disabled={!canEdit} onChange={(e) => set({ y_tick_step: num(e.target.value) })} /></label>
                {!isGroup && <label className="field"><span>{lang === "fr" ? "Pas X" : "X step"}</span>
                  <input type="number" step="any" value={cfg.x_tick_step ?? ""} disabled={!canEdit} onChange={(e) => set({ x_tick_step: num(e.target.value) })} /></label>}
              </div>
            </div>
          </details>
          <details>
            <summary>{t("style")}</summary>
            <div>
              <label className="field"><span>{t("palette")}</span>
                <select value={cfg.palette ?? "okabe_ito"} disabled={!canEdit} onChange={(e) => set({ palette: e.target.value })}>
                  <option value="okabe_ito">Okabe–Ito</option><option value="tol_bright">Tol bright</option><option value="tol_muted">Tol muted</option><option value="grayscale">Grayscale / Niveaux de gris</option>
                </select></label>
              <div className="row tight" style={{ marginBottom: 10 }}>{palette.map((c) => <span key={c} className="swatch" style={{ background: c, display: "inline-block" }} />)}</div>
              <div className="grid2">
                <label className="field"><span>{t("font")}</span>
                  <select value={cfg.font_family ?? "Arial"} disabled={!canEdit} onChange={(e) => set({ font_family: e.target.value })}>
                    {["Arial", "Helvetica", "Times New Roman", "DejaVu Sans"].map((f) => <option key={f}>{f}</option>)}</select></label>
                {field("fontSize", "font_size_pt", 0.5)}
                {field("titleSize", "title_size_pt", 0.5)}
                {field("lineWidth", "line_width_pt", 0.25)}
                {field("pointSize", "point_size_pt", 0.5)}
                <label className="field"><span>{t("pointShape")}</span>
                  <select value={cfg.point_shape ?? "circle"} disabled={!canEdit} onChange={(e) => set({ point_shape: e.target.value })}>
                    {["circle", "open_circle", "square", "triangle", "diamond"].map((s) => <option key={s} value={s}>{({ circle: "●", open_circle: "○", square: "■", triangle: "▲", diamond: "◆" } as Record<string, string>)[s]}</option>)}</select></label>
              </div>
              {!isGroup && <label className="field"><span>{t("legend")}</span>
                <select value={String(cfg.legend ?? "auto")} disabled={!canEdit} onChange={(e) => set({ legend: e.target.value === "auto" ? "auto" : e.target.value === "true" })}>
                  <option value="auto">{t("auto")}</option><option value="true">✓</option><option value="false">✕</option></select></label>}
            </div>
          </details>
          <details open>
            <summary>{t("annotations")}</summary>
            <div>
              {isGroup && (
                <>
                  <div className="stars-box">
                    <b>★ {lang === "fr" ? "Étoiles de significativité automatiques" : "Automatic significance stars"}</b>
                    <div className="grid2" style={{ marginTop: 6 }}>
                      <label className="field"><span>{lang === "fr" ? "Comparer" : "Compare"}</span>
                        <select value={starsOpts.mode} onChange={(e) => setStarsOpts({ ...starsOpts, mode: e.target.value })}>
                          <option value="all">{lang === "fr" ? "Toutes les paires" : "All pairs"}</option>
                          <option value="control">{lang === "fr" ? "Chaque groupe vs contrôle" : "Each group vs control"}</option></select></label>
                      {starsOpts.mode === "control" ? <label className="field"><span>{lang === "fr" ? "Contrôle" : "Control"}</span>
                        <select value={starsOpts.control || order[0]} onChange={(e) => setStarsOpts({ ...starsOpts, control: e.target.value })}>
                          {order.map((g) => <option key={g}>{g}</option>)}</select></label> : <div />}
                      <label className="field"><span>{t("testUsed")}</span>
                        <select value={starsOpts.test} onChange={(e) => setStarsOpts({ ...starsOpts, test: e.target.value })}>
                          <option value="welch">{lang === "fr" ? "t de Welch (non apparié)" : "Welch t (unpaired)"}</option>
                          <option value="student">{lang === "fr" ? "t de Student" : "Student t"}</option>
                          <option value="mannwhitney">Mann–Whitney</option>
                          <option value="paired">{lang === "fr" ? "t apparié" : "Paired t"}</option>
                          <option value="wilcoxon">{lang === "fr" ? "Wilcoxon apparié" : "Wilcoxon paired"}</option></select></label>
                      <label className="field"><span>{lang === "fr" ? "Correction multiple" : "Multiple-comparison correction"}</span>
                        <select value={starsOpts.correction} onChange={(e) => setStarsOpts({ ...starsOpts, correction: e.target.value })}>
                          <option value="holm">Holm–Bonferroni ({lang === "fr" ? "recommandé" : "recommended"})</option>
                          <option value="bonferroni">Bonferroni</option>
                          <option value="none">{lang === "fr" ? "Aucune" : "None"}</option></select></label>
                    </div>
                    <div className="field"><span>{lang === "fr" ? "Afficher sur la figure" : "Show on the figure"}</span><PFormat cfg={cfg} set={set} canEdit={canEdit} /></div>
                    {figurePFormat(cfg) === "stars" && <StarThresholds cfg={cfg} set={set} canEdit={canEdit} />}
                    <label className="check"><input type="checkbox" disabled={!canEdit} checked={!!cfg.hide_ns} onChange={(e) => set({ hide_ns: e.target.checked })} /> {lang === "fr" ? "Masquer les comparaisons « ns »" : "Hide “ns” comparisons"}</label>
                    <button className="primary small" disabled={!canEdit || starsBusy || order.length < 2} onClick={autoStars}>
                      {starsBusy ? "…" : annotations.some((a) => a.type === "pvalue" && a.all) ? (lang === "fr" ? "Recalculer les étoiles" : "Recompute stars") : (lang === "fr" ? "Ajouter les étoiles" : "Add stars")}</button>
                    <p className="small-text muted" style={{ marginTop: 6 }}>{lang === "fr"
                      ? "Crée une analyse enregistrée « comparaisons deux à deux » (visible dans Analyses), avec la correction indiquée dans la légende."
                      : "Creates a saved “pairwise comparisons” analysis (listed under Analyses); the correction is stated in the caption."}</p>
                  </div>
                  <p className="small-text muted">{t("pOnlyFromAnalysis")}</p>
                  {annotations.map((a, i) => a.type === "pvalue" && !a.all ? (
                    <div key={i} className="row" style={{ marginBottom: 6 }}>
                      <select className="grow" value={a.analysis_id} disabled={!canEdit} onChange={(e) => { const n = [...annotations]; n[i] = { ...a, analysis_id: e.target.value }; set({ annotations: n }); }}>
                        {pAnalyses.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.last_result!.result.comparisons![0].group1} vs {x.last_result!.result.comparisons![0].group2}) {x.status === "stale" ? `— ${t("stale")}` : ""}</option>)}
                      </select>
                      <button className="ghost small" disabled={!canEdit} onClick={() => set({ annotations: annotations.filter((_, j) => j !== i) })}>×</button>
                    </div>
                  ) : null)}
                  {pAnalyses.length === 0 ? <p className="small-text muted">{t("noAnalysisForP")}</p>
                    : <button className="small" disabled={!canEdit} onClick={() => set({ annotations: [...annotations, { type: "pvalue", analysis_id: pAnalyses[0].id }] })}>+ {t("addPValue")}</button>}
                  <hr style={{ border: "none", borderTop: "1px solid var(--line-2)", margin: "10px 0" }} />
                </>
              )}
              {annotations.map((a, i) => a.type === "text" ? (
                <div key={i} className="row" style={{ marginBottom: 6 }}>
                  <span className="badge manual">{t("manualTag")}</span>
                  <RichInput className="grow" value={a.text ?? ""} disabled={!canEdit} onChange={(v) => { const n = [...annotations]; n[i] = { ...a, text: v }; set({ annotations: n }); }} />
                  <input type="number" style={{ width: 58 }} step={0.05} min={0} max={1} value={a.x} title="x (0–1)" disabled={!canEdit} onChange={(e) => { const n = [...annotations]; n[i] = { ...a, x: Number(e.target.value) }; set({ annotations: n }); }} />
                  <input type="number" style={{ width: 58 }} step={0.05} min={0} max={1} value={a.y} title="y (0–1)" disabled={!canEdit} onChange={(e) => { const n = [...annotations]; n[i] = { ...a, y: Number(e.target.value) }; set({ annotations: n }); }} />
                  <button className="ghost small" disabled={!canEdit} onClick={() => set({ annotations: annotations.filter((_, j) => j !== i) })}>×</button>
                </div>
              ) : null)}
              <button className="small" disabled={!canEdit} onClick={() => set({ annotations: [...annotations, { type: "text", text: "A", x: 0.05, y: 0.06, manual: true }] })}>+ {t("addText")}</button>
              <p className="small-text muted" style={{ marginTop: 6 }}>{t("manualHelp")}</p>
              <span className="badge soon">{t("multiPanel")} — {t("comingSoon")}</span>
            </div>
          </details>
          <details open>
            <summary>{t("export")}</summary>
            <div>
              <div className="grid2">
                <label className="field"><span>{t("background")}</span>
                  <select value={bg} onChange={(e) => setBg(e.target.value as "white" | "transparent")}><option value="white">{t("white")}</option><option value="transparent">{t("transparent")}</option></select></label>
                <label className="field"><span>{t("dpi")}</span>
                  <select value={dpi} onChange={(e) => setDpi(Number(e.target.value))}>{[150, 300, 600, 1200].map((d) => <option key={d}>{d}</option>)}</select></label>
              </div>
              <p className="small-text muted">PNG : {pngPx(w)} × {pngPx(h)} {t("pixels")}</p>
              <div className="row">
                <a className={`btn ${saved ? "" : "disabled"}`} href={exportUrl("svg")} aria-disabled={!saved}>SVG</a>
                <a className="btn" href={exportUrl("pdf")} aria-disabled={!saved}>PDF</a>
                <a className="btn primary" href={exportUrl("png")} aria-disabled={!saved}>PNG</a>
              </div>
            </div>
          </details>
        </div>
        <div>
          {renderErr && <div className="notice error" style={{ marginBottom: 8 }}>{renderErr}{svg && <span className="small-text"> {lang === "fr" ? "(dernier aperçu valide affiché)" : "(last valid preview shown)"}</span>}</div>}
          {saveErr && <div className="notice error row" style={{ marginBottom: 8 }}><span className="grow">{saveErr}</span>
            <button className="small" onClick={retrySave}>{lang === "fr" ? "Réessayer (garder ma version)" : "Retry (keep my version)"}</button>
            <button className="small" onClick={() => window.location.reload()}>{lang === "fr" ? "Recharger la figure" : "Reload figure"}</button></div>}
          <div className="fig-canvas">
            {svg ? (
              <FigureCanvas svg={svg} widthMm={w} zoom={zoom} selected={selected} canEdit={canEdit} rowLabel={rowLabel}
                onSelect={setSelected} onDrag={onDrag} onInlineEdit={onInlineEdit} />
            ) : !renderErr && <span className="muted small-text">…</span>}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <span className="small-text muted grow">{t("previewNote")} {lang === "fr" ? "Cliquez un élément pour le modifier, double-cliquez un texte pour l’éditer, glissez titre, légende et textes." : "Click an element to edit it, double-click a text to edit it in place, drag the title, legend and texts."}</span>
            <div className="seg" role="group" aria-label="zoom">{[1, 1.5, 2, 3].map((z) => <button key={z} className={zoom === z ? "on" : ""} aria-pressed={zoom === z} onClick={() => setZoom(z)}>{z * 100} %</button>)}</div>
          </div>
          {selected && <div style={{ marginTop: 10 }}><Inspector sel={selected} cfg={cfg} set={set} canEdit={canEdit} isGroup={isGroup}
            colorOf={(g) => (cfg.colors ?? {})[g] ?? palette[Math.max(0, order.indexOf(g)) % palette.length]} rowLabel={rowLabel}
            onShowRow={(r) => nav(`/p/${pid}/data/${fig.dataset_id}?row=${encodeURIComponent(r)}`)} onExcludeRow={excludeRow} onClose={() => setSelected(null)} /></div>}
          {warnings.map((w, i) => <div key={i} className="notice warn" style={{ marginTop: 8 }}>{L(w)}</div>)}
          {caption && <div className="panel" style={{ marginTop: 10 }}><h4>{t("caption")}</h4><p className="small-text" style={{ margin: 0 }}>{caption}</p></div>}
        </div>
      </div>
    </>
  );
}
