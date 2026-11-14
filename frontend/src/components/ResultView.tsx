import { useState } from "react";
import { AnalysisResultData } from "../api";
import { fmtNum, fmtP, useI18n } from "../i18n";

export default function ResultView({ result: r, meta, stale }: { result: AnalysisResultData; meta: { dataset_version: number; created_at: string }; stale: boolean }) {
  const { t, L, lang } = useI18n();
  const [copied, setCopied] = useState(false);
  const n = (v: number | null | undefined, d = 4) => fmtNum(v, lang, d);
  const ci = (c: [number, number] | null | undefined) => (c ? `[${n(c[0])} ; ${n(c[1])}]` : "—");

  return (
    <div className="panel" style={stale ? { borderColor: "#e9c46a" } : undefined}>
      <div className="row" style={{ marginBottom: 8 }}>
        <h3 className="grow" style={{ margin: 0 }}>{t("results")} — {L(r.title)}</h3>
        <span className="small-text muted">
          {t("computedOn")} {meta.dataset_version} {t("ofTable")} « {r.dataset?.name} » · {new Date(meta.created_at).toLocaleString()}
        </span>
      </div>
      {stale && <div className="notice warn">{t("staleExplain")}</div>}

      {r.status === "error" && (
        <div className="notice error"><b>{t("errors")}</b><ul>{r.errors.map((e, i) => <li key={i}>{L(e)}</li>)}</ul></div>
      )}
      {r.warnings.length > 0 && (
        <div className="notice warn"><b>{t("warnings")}</b><ul>{r.warnings.map((e, i) => <li key={i}>{L(e)}</li>)}</ul></div>
      )}

      {r.status === "ok" && r.test && (
        <div className="result-head">
          <div className="big-stat">{t("testUsed")}<b style={{ fontSize: 15 }}>{L(r.test.name)}</b>
            {r.test.method && <span className="small-text muted">p : {r.test.method}</span>}</div>
          <div className="big-stat">{r.test.statistic_name}<b>{n(r.test.statistic)}</b></div>
          {r.test.df !== null && r.test.df !== undefined && <div className="big-stat">{t("df")}<b>{n(r.test.df)}</b></div>}
          <div className="big-stat">{t("pValue")} ({r.test.alternative === "two-sided" ? t("twoSided").toLowerCase() : r.test.alternative})<b>{fmtP(r.test.p_display, lang)}</b></div>
          {r.effect_size && <div className="big-stat">{L(r.effect_size.name)}<b>{n(r.effect_size.value, 3)}</b>
            <span className="small-text muted">{r.effect_size.ci ? `${t("ci")} ${Math.round((r.effect_size.ci_level ?? 0.95) * 100)} % ${ci(r.effect_size.ci)}` : L(r.effect_size.note) || ""}</span></div>}
        </div>
      )}
      {r.status === "ok" && r.estimate && (
        <p>{L(r.estimate.name)} : <b>{n(r.estimate.value)}</b>{r.estimate.ci && <> · {t("ci")} {Math.round((r.estimate.ci_level ?? 0.95) * 100)} % {ci(r.estimate.ci)}</>}</p>
      )}

      {r.groups && r.groups.length > 0 && (
        <div style={{ overflowX: "auto", marginBottom: 12 }}>
          <table className="stats">
            <thead><tr>
              <th>{r.kind === "correlation" || r.kind === "linear_regression" ? "" : t("group")}</th><th>n</th><th>{t("mean")}</th><th>SD / ET</th><th>SEM / ESM</th><th>{t("ci")} {Math.round(((r.groups[0]?.ci_level as number) ?? 0.95) * 100)} %</th>
              <th>{t("median")}</th><th>Q1–Q3</th><th>min–max</th><th>{t("missing")}</th><th>{t("excluded")}</th>
            </tr></thead>
            <tbody>{r.groups.map((g) => (
              <tr key={g.name}>
                <td>{g.name}</td><td>{g.n}</td><td>{n(g.mean)}</td><td>{n(g.sd)}</td><td>{n(g.sem)}</td>
                <td>{g.ci_low !== undefined ? ci([g.ci_low, g.ci_high]) : "—"}</td><td>{n(g.median)}</td>
                <td>{g.q1 !== undefined ? `${n(g.q1)} – ${n(g.q3)}` : "—"}</td><td>{g.min !== undefined ? `${n(g.min)} – ${n(g.max)}` : "—"}</td>
                <td>{g.n_missing ?? "—"}</td><td>{g.n_excluded ?? "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {r.kind === "pairwise" && r.comparisons && r.comparisons.length > 0 && (
        <div style={{ overflowX: "auto", marginBottom: 12 }}>
          <h4 style={{ marginTop: 0 }}>{lang === "fr" ? "Comparaisons" : "Comparisons"}{r.family && <span className="small-text muted"> · {r.family.size} · {lang === "fr" ? "correction" : "correction"} : {r.family.correction === "none" ? (lang === "fr" ? "aucune" : "none") : r.family.correction === "holm" ? "Holm" : "Bonferroni"}</span>}</h4>
          <table className="stats">
            <thead><tr><th>{lang === "fr" ? "Comparaison" : "Comparison"}</th><th>{lang === "fr" ? "Différence" : "Difference"}</th><th>{lang === "fr" ? "Statistique" : "Statistic"}</th><th>{t("df")}</th><th>{lang === "fr" ? "p brut" : "raw p"}</th><th>{lang === "fr" ? "p ajusté" : "adjusted p"}</th><th></th></tr></thead>
            <tbody>{r.comparisons.map((c, i) => (
              <tr key={i}><td>{c.group1} vs {c.group2}</td><td>{n(c.estimate ?? null)}</td><td>{c.statistic_name} = {n(c.statistic ?? null)}</td><td>{n(c.df ?? null)}</td>
                <td>{c.p_raw_display ? fmtP(c.p_raw_display, lang) : "—"}</td><td><b>{fmtP(c.p_display, lang)}</b></td>
                <td>{c.p < 0.0001 ? "****" : c.p < 0.001 ? "***" : c.p < 0.01 ? "**" : c.p < 0.05 ? "*" : "ns"}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {r.coefficients && (
        <table className="stats" style={{ marginBottom: 12 }}>
          <thead><tr><th></th><th>{t("estimate")}</th><th>SE</th><th>t</th><th>{t("df")}</th><th>p</th><th>{t("ci")}</th></tr></thead>
          <tbody>{r.coefficients.map((c, i) => (
            <tr key={i}><td>{L(c.name)}</td><td>{n(c.estimate)}</td><td>{n(c.se)}</td><td>{n(c.t)}</td><td>{n(c.df)}</td><td>{fmtP(c.p_display, lang)}</td><td>{ci(c.ci)}</td></tr>
          ))}</tbody>
        </table>
      )}
      {r.fit && <p className="small-text">R² = {n(r.fit.r_squared)} · R² adj. = {n(r.fit.adj_r_squared)} · σ = {n(r.fit.sigma)} · F(1, {n(r.fit.f_df?.[1])}) = {n(r.fit.f)} · n = {r.n}</p>}

      {r.assumptions && r.assumptions.length > 0 && (
        <>
          <h4>{t("assumptions")}</h4>
          <ul className="small-text" style={{ marginTop: 0 }}>{r.assumptions.map((x, i) => <li key={i}>{L(x)}</li>)}</ul>
        </>
      )}
      {r.diagnostics && r.diagnostics.length > 0 && (
        <>
          <h4>{t("diagnostics")}</h4>
          <table className="stats small-text" style={{ marginBottom: 6 }}><tbody>
            {r.diagnostics.map((d, i) => <tr key={i}><td>{L(d.name)}</td><td>{d.statistic_name} = {n(d.statistic)}</td><td>p = {fmtP(d.p_display, lang)}</td></tr>)}
          </tbody></table>
          {r.diagnostics_note && <p className="small-text muted">{L(r.diagnostics_note)}</p>}
        </>
      )}
      {r.method_text && (
        <>
          <div className="row" style={{ marginTop: 10 }}><h4 className="grow" style={{ margin: 0 }}>{t("methodsText")}</h4>
            <button className="small" onClick={() => { navigator.clipboard?.writeText(L(r.method_text) + (r.result_text ? ` ${L(r.result_text)}.` : "")); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? t("copied") : t("copy")}</button></div>
          <div className="methods" style={{ marginTop: 6 }}>{L(r.method_text)} {r.result_text && <b>{lang === "fr" ? L(r.result_text).replace(/(\d)\.(\d)/g, "$1,$2") : L(r.result_text)}.</b>}</div>
        </>
      )}
      <p className="small-text muted" style={{ marginTop: 8 }}>{r.engine}</p>
    </div>
  );
}
