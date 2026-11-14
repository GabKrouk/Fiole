import { ReactNode } from "react";
import { useI18n } from "../i18n";
import type { Selection } from "./FigureCanvas";

type Cfg = Record<string, any>;

interface Props {
  sel: Selection;
  cfg: Cfg;
  set: (p: Cfg) => void;
  canEdit: boolean;
  isGroup: boolean;
  colorOf: (name: string) => string;
  rowLabel: (rowId: string) => string;
  onShowRow: (rowId: string) => void;
  onExcludeRow: (rowId: string) => void;
  onClose: () => void;
}

// server-side defaults, shown as placeholders when the figure does not override them
const DEF: Record<string, number> = { point_size_pt: 4, fill_opacity: 0.3, line_width_pt: 0.75, width_mm: 80, height_mm: 70, font_size_pt: 8, title_size_pt: 9, tick_length_pt: 3 };
const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));

export default function Inspector({ sel, cfg, set, canEdit, isGroup, colorOf, rowLabel, onShowRow, onExcludeRow, onClose }: Props) {
  const { t, lang } = useI18n();
  const fr = lang === "fr";
  const T = (f: string, e: string) => (fr ? f : e);
  const N = (label: string, key: string, step = 0.25, min?: number) => (
    <label className="field"><span>{label}</span>
      <input type="number" step={step} min={min} value={cfg[key] ?? ""} placeholder={DEF[key] !== undefined ? String(DEF[key]) : "auto"} disabled={!canEdit} onChange={(e) => set({ [key]: num(e.target.value) })} /></label>
  );
  const S = (label: string, key: string, opts: [string, string][], def: string) => (
    <label className="field"><span>{label}</span>
      <select value={String(cfg[key] ?? def)} disabled={!canEdit} onChange={(e) => set({ [key]: isNaN(Number(e.target.value)) || e.target.value === "" ? e.target.value : Number(e.target.value) })}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select></label>
  );
  const Txt = (label: string, key: string) => (
    <label className="field"><span>{label}</span><input value={cfg[key] ?? ""} disabled={!canEdit} onChange={(e) => set({ [key]: e.target.value })} /></label>
  );
  const ticks = (
    <div className="grid3">
      {S(T("Graduations", "Tick marks"), "tick_direction", [["out", T("vers l’extérieur", "outside")], ["in", T("vers l’intérieur", "inside")], ["none", T("aucune", "none")]], "out")}
      {N(T("Longueur (pt)", "Length (pt)"), "tick_length_pt", 0.5, 0)}
      {S(T("Graduations mineures", "Minor ticks"), "minor_ticks", [["0", "0"], ["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["9", "9"]], "0")}
    </div>
  );

  let title = "";
  let body: ReactNode = null;
  switch (sel.el) {
    case "title":
      title = T("Titre", "Title");
      body = <>{Txt(T("Texte", "Text"), "title")}<div className="grid2">{N(T("Taille (pt)", "Size (pt)"), "title_size_pt", 0.5)}
        <div className="field"><span>&nbsp;</span><button className="small" disabled={!canEdit} onClick={() => set({ title_dx: 0, title_dy: 0 })}>{T("Recentrer", "Re-centre")}</button></div></div>
        <p className="small-text muted">{T("Glissez le titre pour le déplacer. Double-cliquez pour l’éditer.", "Drag the title to move it. Double-click to edit it.")}</p></>;
      break;
    case "y-axis":
    case "y-label":
      title = T("Axe Y", "Y axis");
      body = <>
        <div className="grid2">{Txt(T("Titre de l’axe", "Axis title"), "y_label")}{Txt(T("Unité", "Unit"), "y_unit")}</div>
        <div className="grid3">{N("min", "y_min")}{N("max", "y_max")}{N(T("Pas des graduations", "Tick spacing"), "y_tick_step")}</div>
        {S(T("Échelle", "Scale"), "y_scale", [["linear", t("scaleLinear")], ["log10", t("scaleLog")]], "linear")}
        {ticks}
        {N(t("fontSize"), "font_size_pt", 0.5)}
      </>;
      break;
    case "x-axis":
    case "x-label":
      title = T("Axe X", "X axis");
      body = <>
        <div className="grid2">{Txt(T("Titre de l’axe", "Axis title"), "x_label")}{!isGroup && Txt(T("Unité", "Unit"), "x_unit")}</div>
        {!isGroup && <><div className="grid3">{N("min", "x_min")}{N("max", "x_max")}{N(T("Pas des graduations", "Tick spacing"), "x_tick_step")}</div>
          {S(T("Échelle", "Scale"), "x_scale", [["linear", t("scaleLinear")], ["log10", t("scaleLog")]], "linear")}</>}
        {ticks}
        {N(t("fontSize"), "font_size_pt", 0.5)}
      </>;
      break;
    case "plot":
    case "frame":
      title = T("Cadre du graphique", "Plot frame");
      body = <>
        <div className="row" style={{ marginBottom: 10 }}>
          <button className={`small ${(cfg.frame ?? "open") === "open" ? "primary" : ""}`} disabled={!canEdit} onClick={() => set({ frame: "open" })}>└ {T("Ouvert (axes X et Y)", "Open (X and Y axes)")}</button>
          <button className={`small ${cfg.frame === "closed" ? "primary" : ""}`} disabled={!canEdit} onClick={() => set({ frame: "closed" })}>▢ {T("Fermé (4 côtés)", "Closed (4 sides)")}</button>
        </div>
        <div className="grid3">{N(t("lineWidth"), "line_width_pt", 0.25, 0.1)}{N(T("Largeur (mm)", "Width (mm)"), "width_mm", 1, 20)}{N(T("Hauteur (mm)", "Height (mm)"), "height_mm", 1, 20)}</div>
        {ticks}
      </>;
      break;
    case "group":
    case "group-label": {
      const g = sel.group ?? "";
      title = `${T("Groupe", "Group")} « ${g} »`;
      body = <>
        <div className="grid2">
          <label className="field"><span>{T("Nom affiché", "Displayed name")}</span>
            <input value={(cfg.group_labels ?? {})[g] ?? g} disabled={!canEdit} onChange={(e) => set({ group_labels: { ...(cfg.group_labels ?? {}), [g]: e.target.value } })} /></label>
          <label className="field"><span>{T("Couleur", "Colour")}</span>
            <input type="color" className="swatch" style={{ width: 44, height: 30 }} value={colorOf(g)} disabled={!canEdit}
              onChange={(e) => set({ colors: { ...(cfg.colors ?? {}), [g]: e.target.value } })} /></label>
        </div>
        <div className="grid3">
          {N(t("pointSize"), "point_size_pt", 0.5, 0.5)}
          {S(t("pointShape"), "point_shape", [["circle", "●"], ["open_circle", "○"], ["square", "■"], ["triangle", "▲"], ["diamond", "◆"]], "circle")}
          {isGroup && N(T("Opacité du remplissage", "Fill opacity"), "fill_opacity", 0.05, 0)}
        </div>
        {sel.row && (
          <div className="notice info" style={{ marginTop: 6 }}>
            <b>{rowLabel(sel.row)}</b> · {sel.x ? `x = ${sel.x} · y = ${sel.value}` : `${T("valeur", "value")} = ${sel.value}`}
            <div className="row" style={{ marginTop: 6 }}>
              <button className="small" onClick={() => onShowRow(sel.row!)}>{T("Voir dans le tableau", "Show in table")}</button>
              {canEdit && <button className="small danger" onClick={() => onExcludeRow(sel.row!)}>{T("Exclure ce point…", "Exclude this point…")}</button>}
            </div>
          </div>
        )}
      </>;
      break;
    }
    case "pvalue": {
      const k = sel.index ?? -1;
      title = T("Significativité", "Significance");
      body = <>
        <div className="row" style={{ marginBottom: 8 }}>
          <PFormat cfg={cfg} set={set} canEdit={canEdit} />
          <label className="check" style={{ marginBottom: 0 }}><input type="checkbox" disabled={!canEdit} checked={!!cfg.hide_ns} onChange={(e) => set({ hide_ns: e.target.checked })} /> {T("Masquer « ns »", "Hide “ns”")}</label>
        </div>
        <StarThresholds cfg={cfg} set={set} canEdit={canEdit} />
        <button className="small danger" disabled={!canEdit} onClick={() => { set({ annotations: (cfg.annotations ?? []).filter((_: unknown, j: number) => j !== k) }); onClose(); }}>{T("Retirer de la figure", "Remove from figure")}</button>
      </>;
      break;
    }
    case "legend":
      title = T("Légende", "Legend");
      body = <div className="row">
        <button className="small" disabled={!canEdit} onClick={() => set({ legend_dx: 0, legend_dy: 0 })}>{T("Position par défaut", "Default position")}</button>
        <button className="small" disabled={!canEdit} onClick={() => { set({ legend: false }); onClose(); }}>{T("Masquer", "Hide")}</button>
        <span className="small-text muted">{T("Glissez la légende pour la déplacer.", "Drag the legend to move it.")}</span>
      </div>;
      break;
    case "annotation": {
      const k = sel.index ?? -1;
      const a = (cfg.annotations ?? [])[k] ?? {};
      const upd = (p: Cfg) => { const n = [...(cfg.annotations ?? [])]; n[k] = { ...a, ...p }; set({ annotations: n }); };
      title = T("Texte manuel", "Manual text");
      body = <>
        <label className="field"><span>{T("Texte", "Text")}</span><input value={a.text ?? ""} disabled={!canEdit} onChange={(e) => upd({ text: e.target.value })} /></label>
        <div className="grid3">
          <label className="field"><span>{T("Taille (pt)", "Size (pt)")}</span><input type="number" step={0.5} value={a.size_pt ?? cfg.font_size_pt ?? 8} disabled={!canEdit} onChange={(e) => upd({ size_pt: num(e.target.value) })} /></label>
          <label className="check" style={{ marginTop: 22 }}><input type="checkbox" checked={!!a.bold} disabled={!canEdit} onChange={(e) => upd({ bold: e.target.checked })} /> {T("Gras", "Bold")}</label>
          <div className="field"><span>&nbsp;</span><button className="small danger" disabled={!canEdit} onClick={() => { set({ annotations: (cfg.annotations ?? []).filter((_: unknown, j: number) => j !== k) }); onClose(); }}>{t("delete")}</button></div>
        </div>
        <p className="small-text muted"><span className="badge manual">{t("manualTag")}</span> {t("manualHelp")}</p>
      </>;
      break;
    }
    default:
      return null;
  }
  return (
    <div className="panel inspector" onClick={(e) => e.stopPropagation()}>
      <div className="row" style={{ marginBottom: 8 }}>
        <h3 className="grow" style={{ margin: 0 }}>✎ {title}</h3>
        <button className="ghost small" onClick={onClose} aria-label={t("close")}>✕</button>
      </div>
      {body}
    </div>
  );
}

export function StarThresholds({ cfg, set, canEdit }: { cfg: Cfg; set: (p: Cfg) => void; canEdit: boolean }) {
  const { lang } = useI18n();
  const th: number[] = cfg.star_thresholds ?? [0.05, 0.01, 0.001, 0.0001];
  return (
    <div className="field"><span>{lang === "fr" ? "Seuils des étoiles (défaut GraphPad)" : "Star thresholds (GraphPad default)"}</span>
      <div className="row tight">
        {th.map((v, i) => (
          <label key={i} className="row tight small-text" style={{ flexWrap: "nowrap" }}>{"*".repeat(i + 1)} p&lt;
            <input type="number" step="any" style={{ width: 72 }} value={v} disabled={!canEdit}
              onChange={(e) => { const n = [...th]; n[i] = Number(e.target.value); set({ star_thresholds: n }); }} /></label>
        ))}
        {cfg.star_thresholds && <button className="ghost small" disabled={!canEdit} onClick={() => set({ star_thresholds: null })}>↺</button>}
      </div>
    </div>
  );
}

/** One label style for the whole figure (same rule as the server renderer). */
export function figurePFormat(cfg: Cfg): "stars" | "exact" {
  if (cfg.p_format === "stars" || cfg.p_format === "exact") return cfg.p_format;
  const anns: Cfg[] = (cfg.annotations ?? []).filter((a: Cfg) => a.type === "pvalue");
  return (anns.find((a) => a.all)?.format ?? anns[0]?.format ?? "stars") === "exact" ? "exact" : "stars";
}

export function PFormat({ cfg, set, canEdit }: { cfg: Cfg; set: (p: Cfg) => void; canEdit: boolean }) {
  const { lang } = useI18n();
  const f = figurePFormat(cfg);
  return (
    <div className="row tight" role="radiogroup" aria-label={lang === "fr" ? "Affichage" : "Display"}>
      <label className="check" style={{ marginBottom: 0 }}><input type="radio" name="p_format" disabled={!canEdit} checked={f === "stars"} onChange={() => set({ p_format: "stars" })} /> ★ {lang === "fr" ? "Étoiles" : "Stars"}</label>
      <label className="check" style={{ marginBottom: 0 }}><input type="radio" name="p_format" disabled={!canEdit} checked={f === "exact"} onChange={() => set({ p_format: "exact" })} /> {lang === "fr" ? "Valeur de p (p = …)" : "p-value (p = …)"}</label>
    </div>
  );
}
