import { ReactNode, useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";

type Dir = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
const DIRS: Dir[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

/**
 * Mouse resizing, GraphPad-style: handles on corners and edges, live "width × height mm" while dragging,
 * Shift keeps the proportions, sizes snap to 1 mm. The new size is applied on release (the server re-renders).
 */
export default function ResizeBox({ widthMm, heightMm, disabled, min = 20, max = 500, onResize, children }: {
  widthMm: number; heightMm: number; disabled?: boolean; min?: number; max?: number;
  onResize: (w: number, h: number) => void; children: ReactNode;
}) {
  const { lang } = useI18n();
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ dir: Dir; x0: number; y0: number; pxPerMm: number } | null>(null);
  const [ghost, setGhost] = useState<{ w: number; h: number; pxPerMm: number; dir: Dir } | null>(null);
  const ghostRef = useRef(ghost);
  ghostRef.current = ghost;

  useEffect(() => {
    function move(e: MouseEvent) {
      const d = drag.current;
      if (!d) return;
      const dx = (e.clientX - d.x0) / d.pxPerMm;
      const dy = (e.clientY - d.y0) / d.pxPerMm;
      // the opposite edge stays put while dragging
      const sx = d.dir.includes("e") ? 1 : d.dir.includes("w") ? -1 : 0;
      const sy = d.dir.includes("s") ? 1 : d.dir.includes("n") ? -1 : 0;
      let w = widthMm + sx * dx;
      let h = heightMm + sy * dy;
      if (e.shiftKey && sx && sy) {
        const r = widthMm / heightMm;
        if (Math.abs(w - widthMm) / widthMm > Math.abs(h - heightMm) / heightMm) h = w / r; else w = h * r;
      }
      const clamp = (v: number) => Math.round(Math.min(max, Math.max(min, v)));
      setGhost({ w: clamp(w), h: clamp(h), pxPerMm: d.pxPerMm, dir: d.dir });
    }
    function up() {
      const d = drag.current, g = ghostRef.current;
      drag.current = null;
      document.body.style.cursor = "";
      if (d && g && (g.w !== Math.round(widthMm) || g.h !== Math.round(heightMm))) onResize(g.w, g.h);
      setGhost(null);
    }
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  }, [widthMm, heightMm, min, max, onResize]);

  function start(dir: Dir, e: React.MouseEvent) {
    if (disabled || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const r = box.current!.getBoundingClientRect();
    drag.current = { dir, x0: e.clientX, y0: e.clientY, pxPerMm: r.width / widthMm };
    document.body.style.cursor = getComputedStyle(e.currentTarget).cursor;
  }

  return (
    <div className="resize-box" ref={box}>
      {children}
      {!disabled && DIRS.map((d) => <span key={d} className={`rz rz-${d}`} onMouseDown={(e) => start(d, e)}
        title={lang === "fr" ? "Glisser pour redimensionner (Maj : garder les proportions)" : "Drag to resize (Shift: keep proportions)"} />)}
      {ghost && <>
        <div className="rz-ghost" style={{ width: ghost.w * ghost.pxPerMm, height: ghost.h * ghost.pxPerMm,
          left: ghost.dir.includes("w") ? (widthMm - ghost.w) * ghost.pxPerMm : 0, top: ghost.dir.includes("n") ? (heightMm - ghost.h) * ghost.pxPerMm : 0 }} />
        <div className="rz-size">{ghost.w} × {ghost.h} mm</div>
      </>}
    </div>
  );
}
