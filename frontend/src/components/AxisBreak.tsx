type Cfg = Record<string, any>;

const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));

/** Broken axis (like GraphPad Prism): hide the values between two numbers, the axis shows // at the gap. */
export default function AxisBreak({ ax, cfg, set, canEdit, fr }: { ax: "x" | "y"; cfg: Cfg; set: (p: Cfg) => void; canEdit: boolean; fr: boolean }) {
  const T = (f: string, e: string) => (fr ? f : e);
  const k = (s: string) => `${ax}_break_${s}`;
  const on = cfg[k("from")] != null || cfg[k("to")] != null;
  const A = ax.toUpperCase();
  return (
    <div className="axis-break" style={{ marginTop: 8 }}>
      <label className="check">
        <input type="checkbox" disabled={!canEdit} checked={on}
          onChange={(e) => set(e.target.checked ? { [k("from")]: cfg[k("from")] ?? 0, [k("to")]: cfg[k("to")] ?? 0 } : { [k("from")]: null, [k("to")]: null, [k("pos")]: null })} />
        {" "}{T(`Couper l’axe ${A} (//)`, `Break the ${A} axis (//)`)}
      </label>
      {on && <>
        <div className="grid3">
          <label className="field"><span>{T("Masquer de", "Hide from")}</span>
            <input type="number" step="any" value={cfg[k("from")] ?? ""} disabled={!canEdit} onChange={(e) => set({ [k("from")]: num(e.target.value) })} /></label>
          <label className="field"><span>{T("à", "to")}</span>
            <input type="number" step="any" value={cfg[k("to")] ?? ""} disabled={!canEdit} onChange={(e) => set({ [k("to")]: num(e.target.value) })} /></label>
          <label className="field"><span>{ax === "y" ? T("Segment du bas (%)", "Lower segment (%)") : T("Segment de gauche (%)", "Left segment (%)")}</span>
            <input type="number" step={5} min={5} max={95} placeholder="auto" value={cfg[k("pos")] ?? ""} disabled={!canEdit} onChange={(e) => set({ [k("pos")]: num(e.target.value) })} /></label>
        </div>
        <p className="small-text muted">{T(
          "Les valeurs entre ces deux nombres sont masquées. « auto » garde la même échelle des deux côtés de la coupure ; un pourcentage donne plus de place à un segment.",
          "Values between these two numbers are hidden. “auto” keeps the same scale on both sides of the break; a percentage gives one segment more room.")}</p>
      </>}
    </div>
  );
}
