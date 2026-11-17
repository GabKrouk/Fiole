import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, Dataset, FigureEntity } from "../api";
import { Key, useI18n } from "../i18n";
import type { ProjectCtx } from "../pages/ProjectPage";
import { groupNames, StepButtons } from "./AnalysisView";
import { factorColumns, levels, numericColumns, stepColumns } from "../columns";
import FigureCanvas, { Selection } from "./FigureCanvas";
import RichInput from "./RichText";
import AxisBreak from "./AxisBreak";
import StyleMenu from "./StyleMenu";
import ResizeBox from "./ResizeBox";
import Modal from "./Modal";
import Inspector, { figurePFormat, PFormat, StarThresholds } from "./Inspector";

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
const Y_KEYS = ["y_min", "y_max", "y_tick_step", "y_scale", "y_label", "y_unit", "y_break_from", "y_break_to", "y_break_pos"];
const GROUP_TYPES = ["scatter_groups", "bar", "box", "violin"];
const XY_TYPES = ["xy", "line"];
const STYLE_KEEP = ["font_family", "font_size_pt", "title_size_pt", "line_width_pt", "point_size_pt", "point_shape", "palette", "colors", "frame",
  "tick_direction", "tick_length_pt", "minor_ticks", "fill_opacity", "title", "background"];

type Cfg = Record<string, any>;
type Warn = { en: string; fr: string; action?: { en: string; fr: string; patch: Cfg } };

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
  const [warnings, setWarnings] = useState<Warn[]>([]);
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
  const [rendering, setRendering] = useState(false);
  const renderCache = useRef(new Map<string, { svg: string; caption: string; warnings: Warn[] }>());
  const [galleryOpen, setGalleryOpen] = useState(() => new URLSearchParams(window.location.search).has("gallery"));
  const [perVarOpen, setPerVarOpen] = useState(false);
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

  const dsVersion = ctx.project.datasets.find((d) => d.id === fig?.dataset_id)?.version ?? 0;
  useEffect(() => {
    if (!fig) return;
    api.get<Dataset>(`/api/projects/${pid}/datasets/${fig.dataset_id}`).then(setDs);
  }, [pid, fig?.dataset_id, dsVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  // p-values linked by hand to saved analyses: re-render only when one of them really changes
  const linkedKey = useMemo(() => {
    const ids = new Set(((cfg.annotations ?? []) as Cfg[]).filter((a) => a.type === "pvalue" && !a.all).map((a) => a.analysis_id));
    return ctx.project.analyses.filter((a) => ids.has(a.id)).map((a) => `${a.id}:${a.version}:${a.last_result?.id ?? ""}`).join("|");
  }, [cfg.annotations, ctx.project.analyses]);

  // Live preview (same renderer as exports). Stars are computed by the server for what is on screen, so
  // they follow every change by themselves. Renders already seen are reused at once (undo, redo, toggles).
  useEffect(() => {
    if (!fig) return;
    const body = { dataset_id: fig.dataset_id, config: { ...cfg, background: bg }, lang };
    const key = JSON.stringify(body) + `#${dsVersion}#${linkedKey}`;
    const show = (r: { svg: string; caption: string; warnings: Warn[] }) => {
      setSvg(r.svg);
      setCaption(r.caption);
      setWarnings(r.warnings);
      setRenderErr("");
    };
    window.clearTimeout(renderTimer.current);
    const hit = renderCache.current.get(key);
    if (hit) {
      ++renderSeq.current;
      setRendering(false);
      show(hit);
      return;
    }
    renderTimer.current = window.setTimeout(async () => {
      const seq = ++renderSeq.current;
      setRendering(true);
      try {
        const r = await api.post<{ svg: string; caption: string; warnings: Warn[] }>(`/api/projects/${pid}/render`, body);
        const cache = renderCache.current;
        cache.set(key, r);
        if (cache.size > 40) cache.delete(cache.keys().next().value!);
        if (seq !== renderSeq.current) return; // a newer render is on its way: never show an outdated figure
        show(r);
      } catch (e) {
        if (seq !== renderSeq.current) return;
        setRenderErr(e instanceof ApiError ? (e.detail?.message ? L(e.detail.message) : JSON.stringify(e.detail)) : String(e));
      } finally {
        if (seq === renderSeq.current) setRendering(false);
      }
    }, 90);
  }, [cfg, fig?.dataset_id, pid, lang, bg, dsVersion, linkedKey, ds]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    const c = ds?.content;
    if (!c) return [];
    const ty = cfg.type ?? "scatter_groups";
    if (GROUP_TYPES.includes(ty)) return cfg.color_by && cfg.group_col && cfg.value_col && cfg.color_by !== cfg.group_col ? levels(c, cfg.color_by) : groupNames(c, cfg);
    const ys: string[] = Array.isArray(cfg.y) ? cfg.y : cfg.y ? [cfg.y] : [];
    const names = (id: string) => c.columns.find((x) => x.id === id)?.name ?? id;
    if (cfg.color_by) return ys.length <= 1 || cfg.y_layout === "panels" ? levels(c, cfg.color_by) : ys.flatMap((y) => levels(c, cfg.color_by).map((l) => `${names(y)} – ${l}`));
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
      const r = await api.put<{ version: number; stars_analysis_id?: string | null }>(`/api/projects/${pid}/figures/${fig!.id}`, { config: job.cfg, version: versionRef.current, ...job.extra });
      versionRef.current = r.version;
      setSaveErr("");
      if (!pendingSave.current) setSaved(true);
      // the server keeps one saved stars analysis per figure, up to date: point the figure at it
      const sid = r.stars_analysis_id;
      const adopt = (c: Cfg): Cfg => (!sid || !((c.annotations ?? []) as Cfg[]).some((a) => a.type === "pvalue" && a.all && a.analysis_id !== sid)) ? c
        : { ...c, annotations: (c.annotations as Cfg[]).map((a) => (a.type === "pvalue" && a.all ? { ...a, analysis_id: sid } : a)) };
      const isNewStars = !!sid && adopt(job.cfg) !== job.cfg;
      if (isNewStars) {
        setCfgState((c) => adopt(c));
        const next = pendingSave.current as { cfg: Cfg; extra: Partial<{ name: string; dataset_id: string }> } | null;
        if (next) pendingSave.current = { ...next, cfg: adopt(next.cfg) };
        undoStack.current = undoStack.current.map(adopt);
        redoStack.current = redoStack.current.map(adopt);
      }
      const touchesP = ((job.cfg.annotations ?? []) as Cfg[]).some((a) => a.type === "pvalue");
      if (job.extra.dataset_id || job.extra.name || isNewStars || touchesP || fig!.stale) ctx.reload();
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
  const set = (p: Cfg, replace = false) => {
    const next = replace ? p : { ...cfg, ...p };
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
      case "y-label":
        if (s.ycol && cfg.y_layout === "panels") return { value: (cfg.y_labels ?? {})[s.ycol] || autoName(s.ycol), commit: (v: string) => set({ y_labels: { ...(cfg.y_labels ?? {}), [s.ycol!]: v } }) };
        return { value: cfg.y_label || autoName(isGroup ? cfg.value_col : (cfg.y ?? [])[0]), commit: (v: string) => set({ y_label: v }) };
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
    // automatic stars stay on: the server recomputes them for the new variable
    const auto = anns.find((a) => a.type === "pvalue" && a.all);
    const savedP = ((saved?.annotations as Cfg[]) ?? []).filter((a) => !(auto && a.all));
    set({
      ...patch,
      ...Object.fromEntries(Y_KEYS.map((k) => [k, saved ? saved[k] ?? null : null])),
      per_y: per,
      annotations: [...anns.filter((a) => a.type !== "pvalue"), ...savedP, ...(auto ? [auto] : [])],
    });
  }

  async function duplicate(columns?: string[]) {
    // save pending edits first, so the copy is exactly what is on screen
    window.clearTimeout(saveTimer.current);
    saveChain.current = saveChain.current.then(flushSave);
    await saveChain.current;
    try {
      const made = await api.post<FigureEntity[]>(`/api/projects/${pid}/figures/${fig!.id}/duplicate`, columns ? { columns } : {});
      await ctx.reload();
      if (made.length) nav(`/p/${pid}/figure/${made[0].id}`);
    } catch (e) {
      setRenderErr(e instanceof ApiError ? (e.detail?.message ? L(e.detail.message) : JSON.stringify(e.detail)) : String(e));
    }
  }

  const type: string = cfg.type ?? "scatter_groups";
  const isGroup = GROUP_TYPES.includes(type);
  const numericCols = numericColumns(ds?.content);
  const factorCols = factorColumns(ds?.content);
  const stepCols = stepColumns(ds?.content);
  // X groups of a bar / box / dot plot: text columns, and numeric columns with a few repeated values (time, dose)
  const groupCols = [...factorCols, ...stepCols];
  const clustered = isGroup && !!cfg.color_by && !!cfg.group_col && !!cfg.value_col && cfg.color_by !== cfg.group_col;
  const xIsStep = isGroup && stepCols.some((c) => c.id === cfg.group_col);
  const yList: string[] = (Array.isArray(cfg.y) ? cfg.y : cfg.y ? [cfg.y] : []).filter((y: string) => y !== cfg.x);
  const yChoices = numericCols.filter((c) => c.id !== cfg.x);
  const panels = yList.length > 1 && cfg.y_layout === "panels";
  // several Y variables: one panel each (A, B, C…) by default, since they rarely share a unit
  function panelSize(n: number): Cfg {
    const cols = Number(cfg.panel_cols) || (n <= 3 ? n : n === 4 ? 2 : 3);
    const rows = Math.ceil(n / Math.min(cols, n));
    return { width_mm: Math.max(w, cols === 1 ? 85 : 180), height_mm: Math.min(240, Math.max(h, rows * 60)) };
  }
  function setYs(ys: string[]) {
    if (!ys.length) return;
    if (ys.length === 1 && yList.length === 1) return switchY({ y: ys });
    const patch: Cfg = { y: ys };
    if (ys.length > 1 && yList.length <= 1 && cfg.y_layout !== "overlay") Object.assign(patch, { y_layout: "panels" }, panelSize(ys.length));
    else if (ys.length > 1 && cfg.y_layout === "panels") Object.assign(patch, panelSize(ys.length));
    set(patch);
  }
  function toggleY(id: string) {
    if (yList.includes(id)) { if (yList.length > 1) setYs(yList.filter((y) => y !== id)); }
    else setYs([...yList, id]);
  }
  function setLayoutMode(m: "panels" | "overlay") {
    set(m === "panels" ? { y_layout: "panels", ...panelSize(yList.length) } : { y_layout: "overlay" });
  }
  const order: string[] = (cfg.group_order as string[] | undefined)?.filter((g) => groups.includes(g)).concat(groups.filter((g) => !(cfg.group_order || []).includes(g))) ?? groups;
  const palette = PALETTES[cfg.palette ?? "okabe_ito"];
  const w = cfg.width_mm ?? 80;
  const h = cfg.height_mm ?? 70;
  const pngPx = (mm: number) => Math.round((mm / 25.4) * dpi);
  const pAnalyses = ctx.project.analyses.filter((a) => a.dataset_id === fig.dataset_id && a.last_result?.result.status === "ok" && (a.last_result.result.comparisons?.length ?? 0) > 0);
  const annotations: Cfg[] = cfg.annotations ?? [];
  // automatic stars: settings live in the figure; older figures fall back to their analysis's settings
  const starsAnn = annotations.find((a) => a.type === "pvalue" && a.all);
  const starsAnCfg = ((starsAnn?.analysis_id ? ctx.project.analyses.find((x) => x.id === starsAnn.analysis_id)?.config : undefined) ?? {}) as Cfg;
  const starsOpts: { mode: string; control: string; test: string; correction: string } = {
    mode: "all", control: "", test: "welch", correction: "holm",
    ...Object.fromEntries(["mode", "control", "test", "correction"].filter((k) => starsAnCfg[k]).map((k) => [k, starsAnCfg[k]])),
    ...(cfg.stars ?? {}),
  };
  const setStars = (p: Partial<typeof starsOpts>) => set({ stars: { ...starsOpts, ...p } });
  const toggleStars = (on: boolean) => set(on
    ? { annotations: [...annotations.filter((a) => !(a.type === "pvalue" && a.all)), { type: "pvalue", all: true }], stars: starsOpts, p_format: figurePFormat(cfg) }
    : { annotations: annotations.filter((a) => !(a.type === "pvalue" && a.all)) });
  const exportUrl = (fmt: string) => `/api/projects/${pid}/figures/${fig.id}/export?format=${fmt}&dpi=${dpi}&background=${bg}&lang=${lang}`;
  const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));
  const field = (k: Key, key: string, step = 0.25) => (
    <label className="field"><span>{t(k)}</span><input type="number" step={step} value={cfg[key] ?? ""} disabled={!canEdit} onChange={(e) => set({ [key]: num(e.target.value) })} /></label>
  );

  return (
    <>
      <div className="main-header fig-header">
        <input className="name-input" value={fig.name} disabled={!canEdit} onChange={(e) => { setFig({ ...fig, name: e.target.value }); persist(cfg, { name: e.target.value }); }} aria-label="name" />
        <span className={`save-state ${saved ? "ok" : ""}`}>{saved ? `✓ ${t("saved")}` : t("saving")}</span>
        <div className="grow" />
        {canEdit && <>
          <button className="small" onClick={undo} disabled={!undoStack.current.length} title="Ctrl/Cmd+Z">↶ {lang === "fr" ? "Annuler" : "Undo"}</button>
          <button className="small" onClick={redo} disabled={!redoStack.current.length} title="Ctrl/Cmd+Shift+Z">↷ {lang === "fr" ? "Rétablir" : "Redo"}</button>
          <button className="small" title={lang === "fr" ? "Copie identique (Cmd/Ctrl+C puis Cmd/Ctrl+V marche aussi)" : "Identical copy (Cmd/Ctrl+C then Cmd/Ctrl+V works too)"}
            onClick={() => duplicate()}>⧉ {lang === "fr" ? "Dupliquer" : "Duplicate"}</button>
          <button className="small primary" onClick={() => setGalleryOpen(true)} title={lang === "fr" ? "Des figures faites avec vos données : cliquez celle que vous voulez" : "Figures made with your data: click the one you want"}>✦ {lang === "fr" ? "Galerie" : "Gallery"}</button>
          <button className="small" onClick={() => setPerVarOpen(true)}>▦ {lang === "fr" ? "Une figure par variable…" : "One figure per variable…"}</button>
          <StyleMenu cfg={cfg} set={set} canEdit={canEdit} builtins={Object.entries(PRESETS).map(([k, p]) => [k, p.label[lang === "fr" ? 0 : 1]])}
            applyBuiltin={(k) => set(PRESETS[k].cfg(Number(cfg.width_mm) || 80, Number(cfg.height_mm) || 70))} />
        </>}
        {canEdit && <button className="small danger ghost" title={t("delete")} onClick={async () => {
          if (!window.confirm(t("confirmDelete"))) return;
          await api.del(`/api/projects/${pid}/figures/${fig.id}`);
          await ctx.reload();
          nav(`/p/${pid}`);
        }}>{t("delete")}</button>}
      </div>
      {galleryOpen && <Gallery pid={pid} datasetId={fig.dataset_id} lang={lang} cfg={cfg} canEdit={canEdit}
        onClose={() => { setGalleryOpen(false); if (window.location.search.includes("gallery")) nav(window.location.pathname, { replace: true }); }}
        onPick={(c) => {
          // keep the look (font, palette, colours) and texts of this figure; take the data mapping of the suggestion
          const keep = Object.fromEntries(Object.entries(cfg).filter(([k]) => STYLE_KEEP.includes(k)));
          lastPush.current = 0;
          set({ ...keep, ...c, annotations: [...((c.annotations as Cfg[]) ?? []), ...annotations.filter((a) => a.type === "text")] }, true);
          setGalleryOpen(false);
          if (window.location.search.includes("gallery")) nav(window.location.pathname, { replace: true });
        }} />}
      {perVarOpen && <PerVariable cols={(GROUP_TYPES.includes(cfg.type ?? "scatter_groups") ? numericCols.filter((c) => c.id !== cfg.value_col) : yChoices.filter((c) => !yList.includes(c.id)))}
        lang={lang} onClose={() => setPerVarOpen(false)} onGo={async (cols) => { await duplicate(cols); setPerVarOpen(false); }} />}
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
                <select value={type} disabled={!canEdit} onChange={(e) => {
                  const ty = e.target.value;
                  const patch: Cfg = { type: ty };
                  if (!GROUP_TYPES.includes(ty)) {
                    // sensible XY start: the figure's value column on Y, another numeric column on X, coloured by its groups
                    const x = cfg.x && numericCols.some((c) => c.id === cfg.x) ? cfg.x : numericCols.find((c) => c.id !== cfg.value_col)?.id ?? numericCols[0]?.id;
                    if (x && x !== cfg.x) patch.x = x;
                    if (!yList.length || (x && yList.includes(x))) { const y = (cfg.value_col && cfg.value_col !== x ? cfg.value_col : numericCols.find((c) => c.id !== x)?.id); if (y) patch.y = [y]; }
                    if (cfg.color_by === undefined && cfg.group_col) patch.color_by = cfg.group_col;
                    // time courses: mean ± SEM with the replicates shown, as in most papers
                    if (ty === "line" && type !== "line") Object.assign(patch, { error: cfg.error && cfg.error !== "sd" ? cfg.error : "sem", center: "mean", show_points: true });
                  }
                  set(patch);
                }}>
                  {[...GROUP_TYPES, ...XY_TYPES].map((k) => <option key={k} value={k}>{t(k as Key)}</option>)}
                  <option disabled>{t("doseResponse")} — {t("comingSoon")}</option>
                </select></label>
              {isGroup && (
                <>
                  <label className="field"><span>{t("xAxisGroups")}</span>
                    <select value={cfg.group_col ?? ""} disabled={!canEdit} onChange={(e) => set({ group_col: e.target.value || null, group_order: null })}>
                      {cfg.value_col ? <option value="">{t("none")}</option> : <option value="">{t("useTableDesign")}</option>}
                      {groupCols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select></label>
                  <label className="field"><span>{lang === "fr" ? "Couleur / sous-groupes (ex. traitement)" : "Colour / sub-groups (e.g. treatment)"}</span>
                    <select value={cfg.color_by ?? ""} disabled={!canEdit || !cfg.group_col || !cfg.value_col} onChange={(e) => set({ color_by: e.target.value || null, group_order: null, ...(e.target.value ? { annotations: annotations.filter((a) => !(a.type === "pvalue" && a.all)), ...(cfg.x_stars ? {} : { x_stars: true, p_format: figurePFormat(cfg) }) } : {}) })}>
                      <option value="">{t("none")}</option>
                      {groupCols.filter((c) => c.id !== cfg.group_col).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                    <span className="small-text muted">{lang === "fr" ? "Côte à côte dans chaque groupe de X : par exemple une barre par traitement à chaque temps." : "Side by side within each X group: e.g. one bar per treatment at each time."}</span></label>
                  {xIsStep && <label className="check"><input type="checkbox" disabled={!canEdit} checked={!!cfg.x_merge} onChange={(e) => set({ x_merge: e.target.checked })} />
                    {lang === "fr" ? "Regrouper les X proches (ex. 21 et 23 min)" : "Pool close X values (e.g. 21 and 23 min)"}</label>}
                  {xIsStep && canEdit && <button type="button" className="small wrap-btn" style={{ marginBottom: 10 }}
                    onClick={() => set({ type: "line", x: cfg.group_col, y: [cfg.value_col], color_by: cfg.color_by ?? null, error: cfg.error && cfg.error !== "sd" ? cfg.error : "sem", center: "mean", show_points: true, group_order: null })}>
                    ↗ {lang === "fr" ? "Voir en courbe (une courbe par sous-groupe)" : "Show as curves (one per sub-group)"}</button>}
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
                  <label className="field"><span>{t("xColumn")}</span>
                    <select value={cfg.x ?? ""} disabled={!canEdit} onChange={(e) => {
                      const x = e.target.value;
                      let y = yList.filter((c) => c !== x);
                      if (!y.length) { const other = numericCols.find((c) => c.id !== x); y = other ? [other.id] : []; }
                      set({ x, y });
                    }}><option value="">—</option>{numericCols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
                  <div className="field"><span>{lang === "fr" ? "Variables Y — cliquez pour en ajouter ou en retirer" : "Y variables — click to add or remove"}</span>
                    <div className="y-chips" role="group" aria-label="Y">
                      {yChoices.map((c) => {
                        const k = yList.indexOf(c.id);
                        return <button key={c.id} type="button" className={`y-chip ${k >= 0 ? "on" : ""}`} aria-pressed={k >= 0} disabled={!canEdit}
                          title={k >= 0 ? (lang === "fr" ? "Retirer" : "Remove") : (lang === "fr" ? "Ajouter" : "Add")} onClick={() => toggleY(c.id)}>
                          {k >= 0 && yList.length > 1 && <b>{String.fromCharCode(65 + k)}</b>}{c.name}</button>;
                      })}
                    </div>
                    <div className="row tight" style={{ marginTop: 4 }}>
                      <button type="button" className="ghost small" disabled={!canEdit || yList.length === yChoices.length} onClick={() => setYs(yChoices.map((c) => c.id))}>{lang === "fr" ? "Toutes" : "All"}</button>
                      <button type="button" className="ghost small" disabled={!canEdit || yList.length <= 1} onClick={() => setYs(yList.slice(0, 1))}>{lang === "fr" ? "Une seule" : "Just one"}</button>
                      {yList.length === 1 && <span className="row tight" style={{ marginLeft: "auto" }} title={lang === "fr" ? "Variable précédente / suivante" : "Previous / next variable"}>
                        <StepButtons cols={yChoices.map((c) => c.id)} value={yList[0]} disabled={!canEdit} onChange={(v) => switchY({ y: [v] })} /></span>}
                    </div>
                  </div>
                  {yList.length > 1 && (
                    <div className="field"><span>{lang === "fr" ? "Plusieurs variables" : "Several variables"}</span>
                      <div className="seg" role="group">
                        <button type="button" className={panels ? "on" : ""} disabled={!canEdit} onClick={() => setLayoutMode("panels")}>▦ {lang === "fr" ? "Un panneau chacune" : "One panel each"}</button>
                        <button type="button" className={!panels ? "on" : ""} disabled={!canEdit} onClick={() => setLayoutMode("overlay")}>≡ {lang === "fr" ? "Superposées" : "Overlaid"}</button>
                      </div>
                      {panels && <label className="row tight small-text" style={{ marginTop: 6 }}>{lang === "fr" ? "Panneaux par ligne" : "Panels per row"}
                        <select value={cfg.panel_cols ?? 0} disabled={!canEdit} onChange={(e) => set({ panel_cols: Number(e.target.value) || null })}>
                          <option value={0}>{t("auto")}</option>{[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}</select>
                        <label className="check" style={{ margin: "0 0 0 8px" }}><input type="checkbox" disabled={!canEdit} checked={cfg.panel_letters ?? true} onChange={(e) => set({ panel_letters: e.target.checked })} /> A, B, C…</label>
                      </label>}
                    </div>
                  )}
                  <label className="field"><span>{t("colorBy")}</span>
                    <select value={cfg.color_by ?? ""} disabled={!canEdit} onChange={(e) => set({ color_by: e.target.value || null, group_order: null })}>
                      <option value="">{t("none")}</option>
                      {factorCols.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                    <span className="small-text muted">{lang === "fr" ? "Une couleur, une courbe et une entrée de légende par modalité (ex. traitement, génotype)." : "One colour, one curve and one legend entry per level (e.g. treatment, genotype)."}</span></label>
                  {type === "line" && <>
                    <label className="check"><input type="checkbox" disabled={!canEdit} checked={!!cfg.x_merge} onChange={(e) => set({ x_merge: e.target.checked })} />
                      {lang === "fr" ? "Regrouper les X proches (ex. 21 et 23 min)" : "Pool close X values (e.g. 21 and 23 min)"}</label>
                    <div className="seg" role="group" style={{ marginTop: 4 }}>
                      {([["linear", "linear", lang === "fr" ? "Linéaire" : "Linear"], ["log10", "linear", "Log X"], ["log10", "log10", "Log-log"]] as const).map(([xs, ys2, lab]) => (
                        <button key={lab} type="button" disabled={!canEdit} className={(cfg.x_scale ?? "linear") === xs && (cfg.y_scale ?? "linear") === ys2 ? "on" : ""}
                          onClick={() => set({ x_scale: xs, y_scale: ys2, x_min: null, x_max: null, y_min: null, y_max: null })}>{lab}</button>))}
                    </div>
                  </>}
                </>
              )}
            </div>
          </details>
          <details open>
            <summary>{t("size")}</summary>
            <div className="grid2">
              <label className="field"><span>{t("width")} (mm)</span><input type="number" min={20} max={500} value={w} disabled={!canEdit} onChange={(e) => set({ width_mm: Number(e.target.value) })} /></label>
              <label className="field"><span>{t("height")} (mm)</span><input type="number" min={20} max={500} value={h} disabled={!canEdit} onChange={(e) => set({ height_mm: Number(e.target.value) })} /></label>
              <div className="size-presets">
                <span className="small-text muted">= {(w / 10).toLocaleString(lang)} × {(h / 10).toLocaleString(lang)} cm</span>
                <div className="seg">{[["85", "1 col."], ["180", "2 col."]].map(([v, l]) => <button key={v} className={String(w) === v ? "on" : ""} disabled={!canEdit} onClick={() => set({ width_mm: Number(v) })}>{l} · {v} mm</button>)}</div>
              </div>
            </div>
          </details>
          <details open>
            <summary>{t("titles")}</summary>
            <div>
              <div className="field"><span>{t("title")}</span><RichInput ariaLabel={t("title")} value={cfg.title ?? ""} disabled={!canEdit} onChange={(v) => set({ title: v })} /></div>
              <div className="grid2">
                {!panels && <>
                  <div className="field"><span>{t("yLabel")}</span><RichInput ariaLabel={t("yLabel")} value={cfg.y_label ?? ""} disabled={!canEdit} onChange={(v) => set({ y_label: v })} /></div>
                  <label className="field"><span>{t("unit")} Y</span><input value={cfg.y_unit ?? ""} disabled={!canEdit} onChange={(e) => set({ y_unit: e.target.value })} /></label>
                </>}
                <div className="field"><span>{t("xLabel")}</span><RichInput ariaLabel={t("xLabel")} value={cfg.x_label ?? ""} disabled={!canEdit} onChange={(v) => set({ x_label: v })} /></div>
                {!isGroup && <label className="field"><span>{t("unit")} X</span><input value={cfg.x_unit ?? ""} disabled={!canEdit} onChange={(e) => set({ x_unit: e.target.value })} /></label>}
              </div>
              {panels && <div className="field"><span>{lang === "fr" ? "Titre de l’axe Y de chaque panneau (vide = nom de la colonne)" : "Y axis title of each panel (empty = column name)"}</span>
                {yList.map((y, k) => {
                  const name = numericCols.find((c) => c.id === y)?.name ?? y;
                  return <div key={y} className="row tight" style={{ marginBottom: 4, flexWrap: "nowrap" }}><b style={{ width: 16 }}>{String.fromCharCode(65 + k)}</b>
                    <RichInput className="grow" ariaLabel={`Y ${name}`} value={(cfg.y_labels ?? {})[y] || name} disabled={!canEdit}
                      onChange={(v) => set({ y_labels: { ...(cfg.y_labels ?? {}), [y]: v === name ? "" : v } })} /></div>;
                })}</div>}
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
              <AxisBreak ax="y" cfg={cfg} set={set} canEdit={canEdit} fr={lang === "fr"} />
              {!isGroup && <AxisBreak ax="x" cfg={cfg} set={set} canEdit={canEdit} fr={lang === "fr"} />}
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
              {isGroup && !clustered && (
                <>
                  <div className={`stars-box ${starsAnn ? "on" : ""}`}>
                    <label className="switch">
                      <input type="checkbox" role="switch" disabled={!canEdit || order.length < 2} checked={!!starsAnn} onChange={(e) => toggleStars(e.target.checked)} />
                      <span className="switch-track" aria-hidden />
                      <b>★ {lang === "fr" ? "Étoiles de significativité" : "Significance stars"}</b>
                    </label>
                    {order.length < 2 && <p className="small-text muted" style={{ margin: "6px 0 0" }}>{lang === "fr" ? "Il faut au moins deux groupes." : "At least two groups are needed."}</p>}
                    {starsAnn && <>
                      <div className="grid2" style={{ marginTop: 10 }}>
                        <label className="field"><span>{lang === "fr" ? "Comparer" : "Compare"}</span>
                          <select value={starsOpts.mode} disabled={!canEdit} onChange={(e) => setStars({ mode: e.target.value })}>
                            <option value="all">{lang === "fr" ? "Toutes les paires" : "All pairs"}</option>
                            <option value="control">{lang === "fr" ? "Chaque groupe vs contrôle" : "Each group vs control"}</option></select></label>
                        {starsOpts.mode === "control" ? <label className="field"><span>{lang === "fr" ? "Contrôle" : "Control"}</span>
                          <select value={order.includes(starsOpts.control) ? starsOpts.control : order[0]} disabled={!canEdit} onChange={(e) => setStars({ control: e.target.value })}>
                            {order.map((g) => <option key={g}>{g}</option>)}</select></label> : <div />}
                      </div>
                      <div>
                        <label className="field"><span>{t("testUsed")}</span>
                          <select value={starsOpts.test} disabled={!canEdit} onChange={(e) => setStars({ test: e.target.value })}>
                            <option value="welch">{lang === "fr" ? "t de Welch (non apparié)" : "Welch t (unpaired)"}</option>
                            <option value="student">{lang === "fr" ? "t de Student" : "Student t"}</option>
                            <option value="mannwhitney">Mann–Whitney</option>
                            <option value="paired">{lang === "fr" ? "t apparié" : "Paired t"}</option>
                            <option value="wilcoxon">{lang === "fr" ? "Wilcoxon apparié" : "Wilcoxon paired"}</option></select></label>
                        <label className="field"><span>{lang === "fr" ? "Correction multiple" : "Multiple-comparison correction"}</span>
                          <select value={starsOpts.correction} disabled={!canEdit} onChange={(e) => setStars({ correction: e.target.value })}>
                            <option value="holm">Holm–Bonferroni ({lang === "fr" ? "recommandé" : "recommended"})</option>
                            <option value="bonferroni">Bonferroni</option>
                            <option value="none">{lang === "fr" ? "Aucune" : "None"}</option></select></label>
                      </div>
                      <div className="field"><span>{lang === "fr" ? "Afficher sur la figure" : "Show on the figure"}</span><PFormat cfg={cfg} set={set} canEdit={canEdit} /></div>
                      {figurePFormat(cfg) === "stars" && <StarThresholds cfg={cfg} set={set} canEdit={canEdit} />}
                      <label className="check"><input type="checkbox" disabled={!canEdit} checked={!!cfg.hide_ns} onChange={(e) => set({ hide_ns: e.target.checked })} /> {lang === "fr" ? "Masquer les comparaisons « ns »" : "Hide “ns” comparisons"}</label>
                      <p className="auto-note">{lang === "fr"
                        ? "Mises à jour automatiquement quand les données, la variable, les groupes ou le test changent. Le détail est enregistré dans Analyses ; la correction est indiquée dans la légende."
                        : "Updated automatically when the data, the variable, the groups or the test change. Details are saved under Analyses; the correction is stated in the caption."}</p>
                    </>}
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
                    : <button className="small wrap-btn" disabled={!canEdit} onClick={() => set({ annotations: [...annotations, { type: "pvalue", analysis_id: pAnalyses[0].id }] })}>+ {t("addPValue")}</button>}
                  <hr style={{ border: "none", borderTop: "1px solid var(--line-2)", margin: "10px 0" }} />
                </>
              )}
              {(type === "line" || clustered) && (
                <>
                  <div className={`stars-box ${cfg.x_stars ? "on" : ""}`}>
                    <label className="switch">
                      <input type="checkbox" role="switch" disabled={!canEdit || order.length < 2} checked={!!cfg.x_stars} onChange={(e) => set({ x_stars: e.target.checked, p_format: figurePFormat(cfg) })} />
                      <span className="switch-track" aria-hidden />
                      <b>★ {lang === "fr" ? (clustered ? "Étoiles dans chaque groupe de X" : "Étoiles à chaque valeur de X") : (clustered ? "Stars within each X group" : "Stars at each X value")}</b>
                    </label>
                    {order.length < 2 && <p className="small-text muted" style={{ margin: "6px 0 0" }}>{lang === "fr" ? "Choisissez d’abord « Couleur / séparer par » (au moins deux groupes)." : "First choose “Colour / split by” (at least two groups)."}</p>}
                    {cfg.x_stars && order.length >= 2 && <>
                      <p className="small-text muted" style={{ margin: "6px 0" }}>{lang === "fr" ? `${(cfg.group_labels ?? {})[order[0]] ?? order[0]} comparé à ${order.length === 2 ? ((cfg.group_labels ?? {})[order[1]] ?? order[1]) : "chacun des autres groupes"} à chaque X (le premier groupe de la liste sert de référence).` : `${order[0]} compared with ${order.length === 2 ? order[1] : "each other group"} at each X (the first group in the list is the reference).`}</p>
                      <label className="field"><span>{t("testUsed")}</span>
                        <select value={cfg.x_stars_test ?? "welch"} disabled={!canEdit} onChange={(e) => set({ x_stars_test: e.target.value })}>
                          <option value="welch">{lang === "fr" ? "t de Welch (non apparié)" : "Welch t (unpaired)"}</option>
                          <option value="student">{lang === "fr" ? "t de Student" : "Student t"}</option>
                          <option value="mannwhitney">Mann–Whitney</option></select></label>
                      <div className="field"><span>{lang === "fr" ? "Afficher sur la figure" : "Show on the figure"}</span><PFormat cfg={cfg} set={set} canEdit={canEdit} /></div>
                      {figurePFormat(cfg) === "stars" && <StarThresholds cfg={cfg} set={set} canEdit={canEdit} />}
                      <label className="check"><input type="checkbox" disabled={!canEdit} checked={!!cfg.hide_ns} onChange={(e) => set({ hide_ns: e.target.checked })} /> {lang === "fr" ? "Masquer les « ns »" : "Hide “ns”"}</label>
                    </>}
                  </div>
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
          <div className={`fig-canvas ${rendering ? "busy" : ""}`}>
            <span className="render-pill" aria-live="polite">{rendering ? (lang === "fr" ? "Mise à jour…" : "Updating…") : ""}</span>
            {svg ? (
              <ResizeBox widthMm={w} heightMm={h} disabled={!canEdit} onResize={(W, H) => set({ width_mm: W, height_mm: H })}>
                <FigureCanvas svg={svg} widthMm={w} zoom={zoom} selected={selected} canEdit={canEdit} rowLabel={rowLabel}
                  onSelect={setSelected} onDrag={onDrag} onInlineEdit={onInlineEdit} />
              </ResizeBox>
            ) : !renderErr && <div className="fig-skeleton" style={{ width: `${w * zoom}mm`, height: `${h * zoom}mm` }} />}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <span className="small-text muted grow">{t("previewNote")} {lang === "fr" ? "Cliquez un élément pour le modifier, double-cliquez un texte pour l’éditer, glissez titre, légende et textes." : "Click an element to edit it, double-click a text to edit it in place, drag the title, legend and texts."}</span>
            <div className="seg" role="group" aria-label="zoom">{[1, 1.5, 2, 3].map((z) => <button key={z} className={zoom === z ? "on" : ""} aria-pressed={zoom === z} onClick={() => setZoom(z)}>{z * 100} %</button>)}</div>
          </div>
          {selected && <div style={{ marginTop: 10 }}><Inspector sel={selected} cfg={cfg} set={set} canEdit={canEdit} isGroup={isGroup}
            colorOf={(g) => (cfg.colors ?? {})[g] ?? palette[Math.max(0, order.indexOf(g)) % palette.length]} rowLabel={rowLabel}
            onShowRow={(r) => nav(`/p/${pid}/data/${fig.dataset_id}?row=${encodeURIComponent(r)}`)} onExcludeRow={excludeRow} onClose={() => setSelected(null)} /></div>}
          {warnings.map((w, i) => <div key={i} className="notice warn row" style={{ marginTop: 8 }}><span className="grow">{L(w)}</span>
            {w.action && canEdit && <button className="small primary" onClick={() => set(w.action!.patch)}>{L(w.action)}</button>}</div>)}
          {caption && <div className="panel" style={{ marginTop: 10 }}><h4>{t("caption")}</h4><p className="small-text" style={{ margin: 0 }}>{caption}</p></div>}
        </div>
      </div>
    </>
  );
}

function PerVariable({ cols, lang, onClose, onGo }: { cols: { id: string; name: string }[]; lang: string; onClose: () => void; onGo: (cols: string[]) => Promise<void> }) {
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [pick, setPick] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={T("Une figure par variable", "One figure per variable")} onClose={onClose} narrow
      footer={<><button onClick={onClose}>{T("Annuler", "Cancel")}</button>
        <button className="primary" disabled={!pick.length || busy} onClick={async () => { setBusy(true); try { await onGo(pick); } finally { setBusy(false); } }}>
          {busy ? "…" : T(`Créer ${pick.length || ""} figure${pick.length > 1 ? "s" : ""}`, `Create ${pick.length || ""} figure${pick.length > 1 ? "s" : ""}`)}</button></>}>
      <p className="small-text muted" style={{ marginTop: 0 }}>{T(
        "Chaque copie garde le style, la taille, les groupes et les textes de cette figure, et montre une autre variable en Y. L’échelle Y repart en automatique et les étoiles sont recalculées pour chaque variable.",
        "Each copy keeps this figure's style, size, groups and texts, and shows another Y variable. The Y scale goes back to automatic and the stars are recomputed for each variable.")}</p>
      <div className="row tight" style={{ marginBottom: 6 }}>
        <button className="ghost small" onClick={() => setPick(cols.map((c) => c.id))}>{T("Tout cocher", "Select all")}</button>
        <button className="ghost small" onClick={() => setPick([])}>{T("Tout décocher", "Clear")}</button>
      </div>
      {cols.map((c) => <label key={c.id} className="check"><input type="checkbox" checked={pick.includes(c.id)}
        onChange={(e) => setPick(e.target.checked ? [...pick, c.id] : pick.filter((x) => x !== c.id))} /> {c.name}</label>)}
      {!cols.length && <div className="empty">{T("Aucune autre colonne numérique.", "No other numeric column.")}</div>}
    </Modal>
  );
}

type Suggestion = { id: string; title: { en: string; fr: string }; description: { en: string; fr: string }; config: Cfg; svg: string };

function Gallery({ pid, datasetId, lang, cfg, canEdit, onClose, onPick }: { pid: string; datasetId: string; lang: string; cfg: Cfg; canEdit: boolean; onClose: () => void; onPick: (c: Cfg) => void }) {
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [items, setItems] = useState<Suggestion[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const style = Object.fromEntries(Object.entries(cfg).filter(([k]) => STYLE_KEEP.includes(k) && k !== "title"));
    api.post<Suggestion[]>(`/api/projects/${pid}/figure-suggestions`, { dataset_id: datasetId, lang, style })
      .then(setItems).catch((e) => setErr(String(e)));
  }, [pid, datasetId, lang]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Modal title={T("Galerie : choisissez un graphe", "Gallery: pick a figure")} onClose={onClose} wide footer={<button onClick={onClose}>{T("Fermer", "Close")}</button>}>
      <p className="small-text muted" style={{ marginTop: 0 }}>{T(
        "Ces figures sont faites avec les données de ce tableau. Cliquez celle qui vous convient : elle remplace la figure en cours (Annuler pour revenir), puis tout reste modifiable.",
        "These figures are made with this table's data. Click the one you want: it replaces the current figure (Undo to go back), and everything stays editable.")}</p>
      {err && <div className="notice error">{err}</div>}
      {!items && !err && <div className="gallery">{[0, 1, 2, 3].map((i) => <div key={i} className="gallery-card skeleton" />)}</div>}
      {items && !items.length && <div className="empty">{T("Pas de suggestion pour ce tableau : il faut au moins une colonne numérique.", "No suggestion for this table: at least one numeric column is needed.")}</div>}
      {items && <div className="gallery">
        {items.map((s) => (
          <button key={s.id} type="button" className={`gallery-card ${s.id === "panels" ? "wide" : ""}`} disabled={!canEdit} onClick={() => onPick(s.config)}>
            <div className="gallery-thumb" dangerouslySetInnerHTML={{ __html: s.svg }} />
            <b>{lang === "fr" ? s.title.fr : s.title.en}</b>
            <span className="small-text muted">{lang === "fr" ? s.description.fr : s.description.en}</span>
          </button>
        ))}
      </div>}
    </Modal>
  );
}
