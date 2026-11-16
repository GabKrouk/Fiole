import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import RichInput from "./RichText";

export interface Selection {
  el: string; // title | y-axis | x-axis | y-label | x-label | frame | plot | group | group-label | pvalue | legend | annotation
  group?: string;
  index?: number;
  row?: string; // clicked data point
  value?: string;
  x?: string;
  ycol?: string; // panel of a multi-panel figure (its Y column)
}

interface Props {
  svg: string;
  widthMm: number;
  zoom: number;
  selected: Selection | null;
  canEdit: boolean;
  rowLabel: (rowId: string) => string;
  onSelect: (s: Selection | null) => void;
  onDrag: (s: Selection, dxPt: number, dyPt: number) => void;
  onInlineEdit: (s: Selection) => { value: string; commit: (v: string) => void } | null;
}

const TEXT_EDITABLE = new Set(["title", "x-label", "y-label", "group-label", "annotation"]);

function selector(s: Selection): string {
  let q = `[data-el="${CSS.escape(s.el)}"]`;
  if (s.group !== undefined) q += `[data-group="${CSS.escape(s.group)}"]`;
  if (s.index !== undefined && (s.el === "pvalue" || s.el === "annotation")) q += `[data-index="${s.index}"]`;
  return q;
}

function readSel(target: Element | null): Selection | null {
  const g = target?.closest("[data-el]") as HTMLElement | null;
  if (!g) return null;
  const s: Selection = { el: g.dataset.el! };
  if (g.dataset.group !== undefined) s.group = g.dataset.group;
  if (g.dataset.index !== undefined) s.index = Number(g.dataset.index);
  const panel = target?.closest("[data-ycol]") as HTMLElement | null;
  if (panel?.dataset.ycol) s.ycol = panel.dataset.ycol;
  const pt = target?.closest("[data-row]") as HTMLElement | null;
  if (pt && pt.dataset.row) {
    s.row = pt.dataset.row;
    s.value = pt.dataset.v;
    s.x = pt.dataset.x;
  }
  return s;
}

/**
 * Interactive view of the server-rendered SVG. The SVG itself is never modified
 * permanently here: clicks select, drags and inline edits change the figure
 * configuration, and the server re-renders. Preview and exports stay identical.
 */
export default function FigureCanvas({ svg, widthMm, zoom, selected, canEdit, rowLabel, onSelect, onDrag, onInlineEdit }: Props) {
  const { t } = useI18n();
  const host = useRef<HTMLDivElement>(null);
  const drag = useRef<{ s: Selection; g: SVGGElement; x0: number; y0: number; scale: number; moved: boolean } | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [edit, setEdit] = useState<{ x: number; y: number; w: number; value: string; commit: (v: string) => void } | null>(null);

  // highlight the selected element
  useEffect(() => {
    const root = host.current;
    if (!root) return;
    root.querySelectorAll(".fig-selected").forEach((n) => n.classList.remove("fig-selected"));
    root.querySelectorAll(".fig-point-selected").forEach((n) => n.classList.remove("fig-point-selected"));
    if (!selected) return;
    root.querySelectorAll(selector(selected)).forEach((n) => n.classList.add("fig-selected"));
    if (selected.row) root.querySelectorAll(`[data-row="${CSS.escape(selected.row)}"]`).forEach((n) => n.classList.add("fig-point-selected"));
  }, [svg, selected]);

  function svgScale(): number {
    const el = host.current?.querySelector("svg");
    if (!el) return 1;
    const vb = el.viewBox.baseVal;
    return vb.width / el.getBoundingClientRect().width;
  }

  function onMouseDown(e: React.MouseEvent) {
    if (!canEdit || e.button !== 0) return;
    const g = (e.target as Element).closest("[data-drag]") as SVGGElement | null;
    if (!g) return;
    const s = readSel(e.target as Element);
    if (!s) return;
    drag.current = { s, g, x0: e.clientX, y0: e.clientY, scale: svgScale(), moved: false };
    e.preventDefault();
  }

  useEffect(() => {
    function move(e: MouseEvent) {
      const d = drag.current;
      if (!d) return;
      const dx = (e.clientX - d.x0) * d.scale;
      const dy = (e.clientY - d.y0) * d.scale;
      if (Math.abs(dx) + Math.abs(dy) > 1) d.moved = true;
      d.g.setAttribute("transform", `translate(${dx.toFixed(2)} ${dy.toFixed(2)})`);
    }
    function up(e: MouseEvent) {
      const d = drag.current;
      if (!d) return;
      drag.current = null;
      if (d.moved) onDrag(d.s, (e.clientX - d.x0) * d.scale, (e.clientY - d.y0) * d.scale);
    }
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [onDrag]);

  function onClick(e: React.MouseEvent) {
    onSelect(readSel(e.target as Element));
  }

  function onDoubleClick(e: React.MouseEvent) {
    if (!canEdit) return;
    const s = readSel(e.target as Element);
    if (!s || !TEXT_EDITABLE.has(s.el)) return;
    const r = onInlineEdit(s);
    if (!r) return;
    const node = (e.target as Element).closest("[data-el]")!.getBoundingClientRect();
    const box = host.current!.getBoundingClientRect();
    setEdit({ x: node.left - box.left, y: node.top - box.top, w: Math.max(node.width + 40, 140), value: r.value, commit: r.commit });
  }

  function onMove(e: React.MouseEvent) {
    const pt = (e.target as Element).closest("[data-row]") as HTMLElement | null;
    if (!pt || !pt.dataset.row) return setTip(null);
    const box = host.current!.getBoundingClientRect();
    const g = (pt.closest("[data-group]") as HTMLElement | null)?.dataset.group;
    const parts = [g, rowLabel(pt.dataset.row), pt.dataset.x ? `x = ${pt.dataset.x}` : null, `${pt.dataset.x ? "y" : t("valueColPick").split(" ")[0]} = ${pt.dataset.v}`].filter(Boolean);
    setTip({ x: e.clientX - box.left + 12, y: e.clientY - box.top + 12, text: parts.join(" · ") });
  }

  return (
    <div className="sheet interactive" ref={host} style={{ width: `${widthMm * zoom}mm`, position: "relative" }}
      onClick={onClick} onDoubleClick={onDoubleClick} onMouseDown={onMouseDown} onMouseMove={onMove} onMouseLeave={() => setTip(null)}>
      <div dangerouslySetInnerHTML={{ __html: svg.replace(/width="[\d.]+mm" height="[\d.]+mm"/, 'width="100%"') }} />
      {tip && <div className="fig-tip" style={{ left: tip.x, top: tip.y }}>{tip.text}</div>}
      {edit && (
        <div className="fig-inline" style={{ left: edit.x, top: Math.max(edit.y - 36, 0), width: Math.max(edit.w, 220) }}
          onClick={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.stopPropagation()}>
          <RichInput autoFocus value={edit.value} ariaLabel="text"
            onCommit={(v) => { edit.commit(v); setEdit(null); }} onCancel={() => setEdit(null)} />
        </div>
      )}
    </div>
  );
}
