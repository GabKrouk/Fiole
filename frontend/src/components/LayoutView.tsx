import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, LayoutEntity } from "../api";
import { useI18n } from "../i18n";
import type { ProjectCtx } from "../pages/ProjectPage";
import ResizeBox from "./ResizeBox";

type Cfg = Record<string, any>;
interface Cell { index: number; row: number; col: number; x: number; y: number; w: number; h: number }

const PAGES: [string, string, number, number][] = [
  ["1col", "1 colonne · 1 column (85 mm)", 85, 110],
  ["15col", "1,5 colonne · 1.5 columns (120 mm)", 120, 100],
  ["2col", "2 colonnes · 2 columns (180 mm)", 180, 120],
  ["a4", "Page A4 (180 × 240 mm utiles)", 180, 240],
];

/** Multi-panel page (Prism "layout"): drop figures in a grid, A/B/C letters, export the page like one figure. */
export default function LayoutView({ ctx }: { ctx: ProjectCtx }) {
  const { layoutId } = useParams();
  const { lang, L } = useI18n();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const nav = useNavigate();
  const pid = ctx.project.id;
  const canEdit = ctx.project.permissions.includes("edit");
  const initial = ctx.project.layouts.find((l) => l.id === layoutId) ?? null;
  const [lay, setLay] = useState<LayoutEntity | null>(initial);
  const [cfg, setCfgState] = useState<Cfg>(initial?.config ?? {});
  const [svg, setSvg] = useState("");
  const [cells, setCells] = useState<Cell[]>([]);
  const [warnings, setWarnings] = useState<{ en: string; fr: string }[]>([]);
  const [err, setErr] = useState("");
  const [zoom, setZoom] = useState(1);
  const [dropOn, setDropOn] = useState<number | null>(null);
  const [saved, setSaved] = useState(true);
  const version = useRef(initial?.version ?? 0);
  const chain = useRef<Promise<void>>(Promise.resolve());
  const pending = useRef<{ cfg?: Cfg; name?: string } | null>(null);
  const timer = useRef<number>();
  const seq = useRef(0);
  const stage = useRef<HTMLDivElement>(null);
  const [split, setSplit] = useState<{ kind: "v" | "h"; i: number; x0: number; fr0: number[]; px: number } | null>(null);

  useEffect(() => {
    const l = ctx.project.layouts.find((x) => x.id === layoutId) ?? null;
    if (l && (!lay || l.id !== lay.id)) { setLay(l); setCfgState(l.config); version.current = l.version; }
  }, [layoutId, ctx.project]); // eslint-disable-line react-hooks/exhaustive-deps

  // preview: the same server composition as the exports
  useEffect(() => {
    if (!lay) return;
    const t = window.setTimeout(async () => {
      const s = ++seq.current;
      try {
        const r = await api.post<{ svg: string; cells: Cell[]; warnings: { en: string; fr: string }[] }>(`/api/projects/${pid}/layouts/render`, { config: cfg, lang });
        if (s !== seq.current) return;
        setSvg(r.svg); setCells(r.cells); setWarnings(r.warnings); setErr("");
      } catch (e) {
        if (s === seq.current) setErr(e instanceof ApiError ? JSON.stringify(e.detail) : String(e));
      }
    }, 150);
    return () => window.clearTimeout(t);
  }, [cfg, lay?.id, pid, lang, ctx.project.figures]); // eslint-disable-line react-hooks/exhaustive-deps

  async function flush() {
    const p = pending.current;
    if (!p || !lay) return;
    pending.current = null;
    try {
      const r = await api.put<LayoutEntity>(`/api/projects/${pid}/layouts/${lay.id}`, { ...(p.cfg ? { config: p.cfg } : {}), ...(p.name ? { name: p.name } : {}), version: version.current });
      version.current = r.version;
      setSaved(true);
      if (p.name) ctx.reload();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        version.current = e.detail.current_version;
        pending.current = { ...p, ...(pending.current ?? {}) };
        return flush();
      }
      setErr(e instanceof ApiError ? JSON.stringify(e.detail) : String(e));
    }
  }
  function persist(p: { cfg?: Cfg; name?: string }) {
    setSaved(false);
    pending.current = { ...(pending.current ?? {}), ...p };
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { chain.current = chain.current.then(flush); }, 500);
  }
  const set = (p: Cfg) => { const next = { ...cfg, ...p }; setCfgState(next); persist({ cfg: next }); };

  const panels: { cell: number; figure_id: string }[] = cfg.panels ?? [];
  const figOf = (cell: number) => panels.find((p) => p.cell === cell)?.figure_id ?? "";
  function place(cell: number, figureId: string) {
    const rest = panels.filter((p) => p.cell !== cell);
    set({ panels: figureId ? [...rest, { cell, figure_id: figureId }].sort((a, b) => a.cell - b.cell) : rest });
  }
  function moveOrSwap(from: number, to: number) {
    const a = figOf(from), b = figOf(to);
    const rest = panels.filter((p) => p.cell !== from && p.cell !== to);
    if (a) rest.push({ cell: to, figure_id: a });
    if (b) rest.push({ cell: from, figure_id: b });
    set({ panels: rest.sort((x, y) => x.cell - y.cell) });
  }
  function grid(rows: number, cols: number) {
    // keep figures in reading order when the grid changes
    const figs = [...panels].sort((a, b) => a.cell - b.cell).map((p) => p.figure_id).slice(0, rows * cols);
    set({ rows, cols, col_widths: null, row_heights: null, panels: figs.map((f, i) => ({ cell: i, figure_id: f })) });
  }

  // drag the gutter between two columns / rows to resize those panels
  const W = Number(cfg.width_mm ?? 180), H = Number(cfg.height_mm ?? 120);
  const pxPerMm = () => (stage.current ? stage.current.getBoundingClientRect().width / W : 1);
  const fr = (vals: any, n: number) => (Array.isArray(vals) && vals.length === n ? vals.map(Number) : Array(n).fill(1));
  useEffect(() => {
    if (!split) return;
    const n = split.kind === "v" ? cfg.cols : cfg.rows;
    const total = split.fr0.reduce((a, b) => a + b, 0);
    const span = (split.kind === "v" ? W : H) - 2 * Number(cfg.margin_mm ?? 2) - Number(cfg.gap_mm ?? 4) * (n - 1);
    const move = (e: MouseEvent) => {
      const dMm = ((split.kind === "v" ? e.clientX : e.clientY) - split.x0) / split.px;
      const d = (dMm / span) * total;
      const next = [...split.fr0];
      const lo = total * 0.08;
      next[split.i] = Math.max(lo, split.fr0[split.i] + d);
      next[split.i + 1] = Math.max(lo, split.fr0[split.i + 1] - d);
      setCfgState((c) => ({ ...c, [split.kind === "v" ? "col_widths" : "row_heights"]: next.map((v) => Math.round(v * 1000) / 1000) }));
    };
    const up = () => { setSplit(null); setCfgState((c) => { persist({ cfg: c }); return c; }); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  }, [split]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!lay) return <div className="empty">{T("Mise en page introuvable.", "Layout not found.")}</div>;
  const figs = ctx.project.figures;
  const pct = (v: number, of: number) => `${(100 * v) / of}%`;
  const exp = (f: string) => `/api/projects/${pid}/layouts/${lay.id}/export?format=${f}&lang=${lang}&dpi=600`;

  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <input className="name-input" value={lay.name} disabled={!canEdit} aria-label="name"
          onChange={(e) => { setLay({ ...lay, name: e.target.value }); persist({ name: e.target.value }); }} />
        <span className="muted small-text grow">{saved ? T("Enregistré", "Saved") : "…"}</span>
        <a className="btn small primary" href={exp("pdf")}>⤓ PDF</a>
        <a className="btn small" href={exp("svg")}>⤓ SVG</a>
        <a className="btn small" href={exp("png")}>⤓ PNG 600 dpi</a>
        {canEdit && <button className="small danger" onClick={async () => {
          if (!window.confirm(T("Supprimer cette mise en page ? Les figures restent.", "Delete this layout? The figures stay."))) return;
          await api.del(`/api/projects/${pid}/layouts/${lay.id}`); await ctx.reload(); nav(`/p/${pid}`);
        }}>{T("Supprimer", "Delete")}</button>}
      </div>
      <div className="fig-layout">
        <div className="fig-controls">
          <details open><summary>{T("Page", "Page")}</summary><div>
            <label className="field"><span>{T("Format", "Size")}</span>
              <select value="" disabled={!canEdit} onChange={(e) => { const p = PAGES.find((x) => x[0] === e.target.value); if (p) set({ width_mm: p[2], height_mm: p[3] }); }}>
                <option value="">{`${W} × ${H} mm`}</option>{PAGES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <div className="grid2">
              <label className="field"><span>{T("Largeur (mm)", "Width (mm)")}</span><input type="number" min={30} max={600} value={W} disabled={!canEdit} onChange={(e) => set({ width_mm: Number(e.target.value) })} /></label>
              <label className="field"><span>{T("Hauteur (mm)", "Height (mm)")}</span><input type="number" min={30} max={600} value={H} disabled={!canEdit} onChange={(e) => set({ height_mm: Number(e.target.value) })} /></label>
              <label className="field"><span>{T("Lignes", "Rows")}</span><input type="number" min={1} max={6} value={cfg.rows ?? 1} disabled={!canEdit} onChange={(e) => grid(Math.max(1, Math.min(6, Number(e.target.value) || 1)), cfg.cols ?? 2)} /></label>
              <label className="field"><span>{T("Colonnes", "Columns")}</span><input type="number" min={1} max={6} value={cfg.cols ?? 2} disabled={!canEdit} onChange={(e) => grid(cfg.rows ?? 1, Math.max(1, Math.min(6, Number(e.target.value) || 1)))} /></label>
              <label className="field"><span>{T("Espace (mm)", "Gap (mm)")}</span><input type="number" min={0} max={40} step={0.5} value={cfg.gap_mm ?? 4} disabled={!canEdit} onChange={(e) => set({ gap_mm: Number(e.target.value) })} /></label>
              <label className="field"><span>{T("Marge (mm)", "Margin (mm)")}</span><input type="number" min={0} max={40} step={0.5} value={cfg.margin_mm ?? 2} disabled={!canEdit} onChange={(e) => set({ margin_mm: Number(e.target.value) })} /></label>
            </div>
            {(cfg.col_widths || cfg.row_heights) && <button className="ghost small" disabled={!canEdit} onClick={() => set({ col_widths: null, row_heights: null })}>↺ {T("Cases de même taille", "Equal cells")}</button>}
          </div></details>
          <details open><summary>{T("Lettres des panneaux", "Panel letters")}</summary><div>
            <label className="check"><input type="checkbox" checked={cfg.label_show ?? true} disabled={!canEdit} onChange={(e) => set({ label_show: e.target.checked })} /> {T("Afficher A, B, C…", "Show A, B, C…")}</label>
            <div className="grid2">
              <label className="field"><span>{T("Style", "Style")}</span>
                <select value={cfg.label_style ?? "A"} disabled={!canEdit} onChange={(e) => set({ label_style: e.target.value })}>
                  {["A", "a", "(A)", "(a)", "A.", "a."].map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
              <label className="field"><span>{T("Taille (pt)", "Size (pt)")}</span><input type="number" min={6} max={36} step={0.5} value={cfg.label_size_pt ?? 12} disabled={!canEdit} onChange={(e) => set({ label_size_pt: Number(e.target.value) })} /></label>
            </div>
            <label className="check"><input type="checkbox" checked={cfg.label_bold ?? true} disabled={!canEdit} onChange={(e) => set({ label_bold: e.target.checked })} /> {T("Gras", "Bold")}</label>
          </div></details>
          <details open><summary>{T("Figures du projet", "Project figures")}</summary><div>
            <p className="small-text muted" style={{ marginTop: 0 }}>{T("Glissez une figure dans une case. Les figures restent liées : une modification de la figure se retrouve ici.", "Drag a figure into a cell. Figures stay linked: changing a figure updates this page.")}</p>
            {figs.map((f) => <button key={f.id} className="fig-chip" draggable={canEdit} disabled={!canEdit}
              onDragStart={(e) => e.dataTransfer.setData("text/fiole-figure", f.id)}
              onClick={() => { const free = cells.find((c) => !figOf(c.index)); if (free) place(free.index, f.id); }}
              title={T("Glisser dans une case, ou cliquer pour la première case libre", "Drag into a cell, or click for the first free cell")}>◩ {f.name}</button>)}
            {!figs.length && <div className="small-text muted">{T("Aucune figure.", "No figures.")}</div>}
          </div></details>
        </div>
        <div>
          {err && <div className="notice error">{err}</div>}
          <div className="fig-canvas">
            {svg && <ResizeBox widthMm={W} heightMm={H} min={30} max={600} disabled={!canEdit} onResize={(w, h) => set({ width_mm: w, height_mm: h })}>
              <div className="layout-stage" ref={stage} style={{ width: `${W * zoom}mm` }}>
                <div dangerouslySetInnerHTML={{ __html: svg.replace(/width="[\d.]+mm" height="[\d.]+mm"/, 'width="100%"') }} />
                {cells.map((c) => {
                  const fid = figOf(c.index);
                  const f = figs.find((x) => x.id === fid);
                  return (
                    <div key={c.index} className={`lay-cell ${f ? "" : "empty"} ${dropOn === c.index ? "drop" : ""}`}
                      style={{ left: pct(c.x, W), top: pct(c.y, H), width: pct(c.w, W), height: pct(c.h, H) }}
                      draggable={!!f && canEdit} onDragStart={(e) => e.dataTransfer.setData("text/fiole-cell", String(c.index))}
                      onDragOver={(e) => { if (canEdit) { e.preventDefault(); setDropOn(c.index); } }} onDragLeave={() => setDropOn(null)}
                      onDrop={(e) => {
                        e.preventDefault(); setDropOn(null);
                        const fromCell = e.dataTransfer.getData("text/fiole-cell");
                        const figId = e.dataTransfer.getData("text/fiole-figure");
                        if (fromCell !== "") moveOrSwap(Number(fromCell), c.index); else if (figId) place(c.index, figId);
                      }}
                      onDoubleClick={() => f && nav(`/p/${pid}/figure/${f.id}`)}
                      title={f ? T("Double-clic : ouvrir la figure · glisser : déplacer", "Double-click: open the figure · drag: move") : ""}>
                      {canEdit && <div className="lay-tools">
                        <select value={fid} aria-label={T("Figure de la case", "Figure in cell")} onChange={(e) => place(c.index, e.target.value)}>
                          <option value="">{f ? T("— vider —", "— empty —") : T("Choisir une figure…", "Choose a figure…")}</option>
                          {figs.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                        </select>
                      </div>}
                    </div>
                  );
                })}
                {canEdit && cells.filter((c) => c.row === 0 && c.col > 0).map((c) => (
                  <div key={"v" + c.col} className={`lay-split v ${split?.kind === "v" && split.i === c.col - 1 ? "on" : ""}`}
                    style={{ left: pct(c.x - Number(cfg.gap_mm ?? 4) / 2, W), top: 0, bottom: 0 }}
                    title={T("Glisser pour changer la largeur des panneaux", "Drag to change panel widths")}
                    onMouseDown={(e) => { e.preventDefault(); setSplit({ kind: "v", i: c.col - 1, x0: e.clientX, fr0: fr(cfg.col_widths, cfg.cols), px: pxPerMm() }); }} />))}
                {canEdit && cells.filter((c) => c.col === 0 && c.row > 0).map((c) => (
                  <div key={"h" + c.row} className={`lay-split h ${split?.kind === "h" && split.i === c.row - 1 ? "on" : ""}`}
                    style={{ top: pct(c.y - Number(cfg.gap_mm ?? 4) / 2, H), left: 0, right: 0 }}
                    title={T("Glisser pour changer la hauteur des panneaux", "Drag to change panel heights")}
                    onMouseDown={(e) => { e.preventDefault(); setSplit({ kind: "h", i: c.row - 1, x0: e.clientY, fr0: fr(cfg.row_heights, cfg.rows), px: pxPerMm() }); }} />))}
              </div>
            </ResizeBox>}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <span className="small-text muted grow">{T("Aperçu à l’échelle, identique à l’export. Glissez les bords de la page ou les espaces entre panneaux pour redimensionner.", "To-scale preview, identical to the export. Drag the page edges or the gaps between panels to resize.")}</span>
            <div className="seg" role="group" aria-label="zoom">{[0.75, 1, 1.5].map((z) => <button key={z} className={zoom === z ? "on" : ""} onClick={() => setZoom(z)}>{z * 100} %</button>)}</div>
          </div>
          {warnings.map((w, i) => <div key={i} className="notice warn" style={{ marginTop: 8 }}>{L(w)}</div>)}
        </div>
      </div>
    </div>
  );
}
