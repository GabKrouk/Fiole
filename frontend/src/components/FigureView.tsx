import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, Dataset, FigureEntity } from "../api";
import { Key, useI18n } from "../i18n";
import type { ProjectCtx } from "../pages/ProjectPage";
import { groupNames } from "./AnalysisView";

const PALETTES: Record<string, string[]> = {
  okabe_ito: ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#56B4E9", "#CC79A7", "#F0E442", "#000000"],
  tol_bright: ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB"],
  tol_muted: ["#332288", "#88CCEE", "#44AA99", "#117733", "#999933", "#DDCC77", "#CC6677", "#882255", "#AA4499"],
  grayscale: ["#1A1A1A", "#737373", "#B3B3B3", "#404040", "#999999", "#D9D9D9"],
};
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
  const [saved, setSaved] = useState(true);
  const [zoom, setZoom] = useState(1.5);
  const [dpi, setDpi] = useState(300);
  const [bg, setBg] = useState<"white" | "transparent">("white");
  const versionRef = useRef(initial?.version ?? 0);
  const saveTimer = useRef<number>();
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
      try {
        const r = await api.post<{ svg: string; caption: string; warnings: { en: string; fr: string }[] }>(`/api/projects/${pid}/render`, { dataset_id: fig.dataset_id, config: { ...cfg, background: bg }, lang });
        setSvg(r.svg);
        setCaption(r.caption);
        setWarnings(r.warnings);
        setRenderErr("");
      } catch (e) {
        setRenderErr(e instanceof ApiError ? (e.detail?.message ? L(e.detail.message) : JSON.stringify(e.detail)) : String(e));
      }
    }, 200);
  }, [cfg, fig?.dataset_id, pid, lang, bg, ctx.project.analyses, ds]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => groupNames(ds?.content), [ds]);

  if (!fig) return null;

  function persist(next: Cfg, extra: Partial<{ name: string; dataset_id: string }> = {}) {
    setSaved(false);
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      try {
        const r = await api.put<{ version: number }>(`/api/projects/${pid}/figures/${fig!.id}`, { config: next, version: versionRef.current, ...extra });
        versionRef.current = r.version;
        setSaved(true);
        if (extra.dataset_id || extra.name) ctx.reload();
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) setRenderErr(t("conflict"));
      }
    }, 500);
  }
  const set = (p: Cfg) => {
    const next = { ...cfg, ...p };
    setCfgState(next);
    persist(next);
  };

  const type: string = cfg.type ?? "scatter_groups";
  const isGroup = GROUP_TYPES.includes(type);
  const numericCols = ds?.content.columns.filter((c) => c.type === "numeric") ?? [];
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
              {!isGroup && (
                <>
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
              <label className="field"><span>{t("title")}</span><input value={cfg.title ?? ""} disabled={!canEdit} onChange={(e) => set({ title: e.target.value })} /></label>
              <div className="grid2">
                <label className="field"><span>{t("yLabel")}</span><input value={cfg.y_label ?? ""} disabled={!canEdit} onChange={(e) => set({ y_label: e.target.value })} /></label>
                <label className="field"><span>{t("unit")} Y</span><input value={cfg.y_unit ?? ""} disabled={!canEdit} onChange={(e) => set({ y_unit: e.target.value })} /></label>
                <label className="field"><span>{t("xLabel")}</span><input value={cfg.x_label ?? ""} disabled={!canEdit} onChange={(e) => set({ x_label: e.target.value })} /></label>
                {!isGroup && <label className="field"><span>{t("unit")} X</span><input value={cfg.x_unit ?? ""} disabled={!canEdit} onChange={(e) => set({ x_unit: e.target.value })} /></label>}
              </div>
            </div>
          </details>
          {isGroup && (
            <details open>
              <summary>{t("groupsOrder")}</summary>
              <div>
                {order.map((g, i) => (
                  <div key={g} className="group-row">
                    <input type="color" className="swatch" aria-label={`color ${g}`} disabled={!canEdit} value={(cfg.colors ?? {})[g] ?? palette[i % palette.length]}
                      onChange={(e) => set({ colors: { ...(cfg.colors ?? {}), [g]: e.target.value } })} />
                    <input value={(cfg.group_labels ?? {})[g] ?? g} disabled={!canEdit} aria-label={`label ${g}`} onChange={(e) => set({ group_labels: { ...(cfg.group_labels ?? {}), [g]: e.target.value } })} />
                    <button className="ghost small" disabled={!canEdit || i === 0} aria-label="up" onClick={() => { const o = [...order]; [o[i - 1], o[i]] = [o[i], o[i - 1]]; set({ group_order: o }); }}>↑</button>
                    <button className="ghost small" disabled={!canEdit || i === order.length - 1} aria-label="down" onClick={() => { const o = [...order]; [o[i + 1], o[i]] = [o[i], o[i + 1]]; set({ group_order: o }); }}>↓</button>
                  </div>
                ))}
                {cfg.colors && Object.keys(cfg.colors).length > 0 && <button className="small" onClick={() => set({ colors: {} })}>↺ {t("palette")}</button>}
                {ds?.content.design?.subject && <label className="check" style={{ marginTop: 8 }}><input type="checkbox" disabled={!canEdit} checked={!!cfg.average_technical} onChange={(e) => set({ average_technical: e.target.checked })} /> {t("avgTech")}</label>}
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
                  <p className="small-text muted">{t("pOnlyFromAnalysis")}</p>
                  {annotations.map((a, i) => a.type === "pvalue" ? (
                    <div key={i} className="row" style={{ marginBottom: 6 }}>
                      <select className="grow" value={a.analysis_id} disabled={!canEdit} onChange={(e) => { const n = [...annotations]; n[i] = { ...a, analysis_id: e.target.value }; set({ annotations: n }); }}>
                        {pAnalyses.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.last_result!.result.comparisons![0].group1} vs {x.last_result!.result.comparisons![0].group2}) {x.status === "stale" ? `— ${t("stale")}` : ""}</option>)}
                      </select>
                      <select value={a.format ?? "exact"} disabled={!canEdit} onChange={(e) => { const n = [...annotations]; n[i] = { ...a, format: e.target.value }; set({ annotations: n }); }} aria-label={t("pFormat")}>
                        <option value="exact">{t("pExact")}</option><option value="stars">{t("pStars")}</option></select>
                      <button className="ghost small" disabled={!canEdit} onClick={() => set({ annotations: annotations.filter((_, j) => j !== i) })}>×</button>
                    </div>
                  ) : null)}
                  {pAnalyses.length === 0 ? <p className="small-text muted">{t("noAnalysisForP")}</p>
                    : <button className="small" disabled={!canEdit} onClick={() => set({ annotations: [...annotations, { type: "pvalue", analysis_id: pAnalyses[0].id, format: "exact" }] })}>+ {t("addPValue")}</button>}
                  <hr style={{ border: "none", borderTop: "1px solid var(--line-2)", margin: "10px 0" }} />
                </>
              )}
              {annotations.map((a, i) => a.type === "text" ? (
                <div key={i} className="row" style={{ marginBottom: 6 }}>
                  <span className="badge manual">{t("manualTag")}</span>
                  <input className="grow" value={a.text} disabled={!canEdit} onChange={(e) => { const n = [...annotations]; n[i] = { ...a, text: e.target.value }; set({ annotations: n }); }} />
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
          <div className="fig-canvas">
            {renderErr ? <div className="notice error">{renderErr}</div> : (
              <div className="sheet" style={{ width: `${w * zoom}mm` }} dangerouslySetInnerHTML={{ __html: svg.replace(/width="[\d.]+mm" height="[\d.]+mm"/, 'width="100%"') }} />
            )}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <span className="small-text muted grow">{t("previewNote")}</span>
            {[1, 1.5, 2, 3].map((z) => <button key={z} className={`small ${zoom === z ? "primary" : ""}`} onClick={() => setZoom(z)}>{z * 100} %</button>)}
          </div>
          {warnings.map((w, i) => <div key={i} className="notice warn" style={{ marginTop: 8 }}>{L(w)}</div>)}
          {caption && <div className="panel" style={{ marginTop: 10 }}><h4>{t("caption")}</h4><p className="small-text" style={{ margin: 0 }}>{caption}</p></div>}
        </div>
      </div>
    </>
  );
}
