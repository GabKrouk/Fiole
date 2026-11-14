"""Analysis implementations. Each returns a JSON-safe result dict.

Every analysis:
* states its assumptions,
* reports n, statistic, df (when defined), p, effect size and CI when available,
* lists warnings / blocking errors explicitly instead of returning a number,
* never chooses the test from a normality test (normality is a diagnostic only),
* generates a reusable methods sentence in EN and FR.
"""
from __future__ import annotations

import math

import numpy as np
import scipy
import statsmodels
from scipy import stats

from ..data import DataError, Group, average_technical, design_for, extract_groups, extract_xy, col_by_id, pair_groups
from ..i18n import msg
from .core import brown_forsythe, clean, describe, fmt, format_p, nct_ci, shapiro

ENGINE = f"fiole-stats 0.1 (SciPy {scipy.__version__}, statsmodels {statsmodels.__version__})"

ALT_TEXT = {
    "two-sided": ("two-sided", "bilatéral"),
    "less": ("one-sided (less)", "unilatéral (inférieur)"),
    "greater": ("one-sided (greater)", "unilatéral (supérieur)"),
}


class AnalysisError(Exception):
    def __init__(self, messages: list[dict]):
        self.messages = messages


def _result(kind: str, title: tuple[str, str]) -> dict:
    return {
        "kind": kind,
        "title": {"en": title[0], "fr": title[1]},
        "status": "ok",
        "errors": [],
        "warnings": [],
        "groups": [],
        "assumptions": [],
        "diagnostics": [],
        "comparisons": [],
        "engine": ENGINE,
    }


def _group_summary(g: Group, ci_level: float) -> dict:
    d = describe(g.values, ci_level)
    d["name"] = g.name
    d["n_missing"] = g.n_missing
    d["n_excluded"] = g.n_excluded
    return d


def _prepare_two_groups(content: dict, config: dict, res: dict) -> tuple[Group, Group]:
    groups = extract_groups(content, design_for(content, config))
    names = config.get("groups") or list(groups.keys())[:2]
    if len(names) != 2:
        raise AnalysisError([msg("need_two_groups")])
    sel = []
    for n in names:
        if n not in groups:
            raise AnalysisError([msg("group_not_found", group=n)])
        sel.append(groups[n])
    if config.get("average_technical"):
        raw = sum(len(g.values) for g in sel)
        sel = [average_technical(g) for g in sel]
        res["warnings"].append(msg("technical_averaged", raw=raw, units=sum(len(g.values) for g in sel)))
    miss = sum(g.n_missing for g in sel)
    if miss:
        res["warnings"].append(msg("missing_dropped", count=miss))
    excl = sum(g.n_excluded for g in sel)
    if excl:
        res["warnings"].append(msg("excluded_dropped", count=excl))
    return sel[0], sel[1]


def _unit_phrase(config: dict, lang: str) -> str:
    unit = (config.get("experimental_unit") or "").strip()
    if not unit:
        return ""
    return f" The experimental unit was the {unit}." if lang == "en" else f" L’unité expérimentale était : {unit}."


def _tech_phrase(config: dict, lang: str) -> str:
    if not config.get("average_technical"):
        return ""
    return (
        " Technical replicates were averaged within each experimental unit before analysis; n refers to experimental units."
        if lang == "en"
        else " Les répétitions techniques ont été moyennées au sein de chaque unité expérimentale avant l’analyse ; n correspond au nombre d’unités expérimentales."
    )


def _n_phrase(groups: list[dict], lang: str) -> str:
    parts = ", ".join(f"{g['name']} n = {g['n']}" for g in groups)
    return f" ({parts})"


def _footer(lang: str) -> str:
    return f" Analyses were performed with Fiole ({ENGINE})." if lang == "en" else f" Les analyses ont été réalisées avec Fiole ({ENGINE})."


def _cohen_d_independent(a: np.ndarray, b: np.ndarray, level: float) -> dict:
    n1, n2 = len(a), len(b)
    sp = math.sqrt(((n1 - 1) * a.var(ddof=1) + (n2 - 1) * b.var(ddof=1)) / (n1 + n2 - 2))
    d = (a.mean() - b.mean()) / sp
    t = d / math.sqrt(1 / n1 + 1 / n2)
    ci = nct_ci(t, n1 + n2 - 2, level)
    factor = math.sqrt(1 / n1 + 1 / n2)
    return {
        "name": {"en": "Cohen's d (pooled SD)", "fr": "d de Cohen (ET combiné)"},
        "symbol": "d",
        "value": d,
        "ci": [ci[0] * factor, ci[1] * factor] if ci else None,
        "ci_level": level,
        "ci_method": {"en": "noncentral t", "fr": "t non centrale"},
    }


# ---------------------------------------------------------------- descriptive


def run_descriptive(content: dict, config: dict) -> dict:
    res = _result("descriptive", ("Descriptive statistics", "Statistiques descriptives"))
    level = float(config.get("ci_level", 0.95))
    groups = extract_groups(content, design_for(content, config))
    names = config.get("groups") or list(groups.keys())
    for n in names:
        if n not in groups:
            raise AnalysisError([msg("group_not_found", group=n)])
        g = groups[n]
        if config.get("average_technical"):
            g = average_technical(g)
        res["groups"].append(_group_summary(g, level))
    pct = int(round(level * 100))
    res["method_text"] = {
        "en": f"Data are summarised as mean, standard deviation (SD), standard error of the mean (SEM = SD/√n) and {pct}% confidence interval of the mean (t distribution), and as median and interquartile range (quartiles by linear interpolation)."
        + _tech_phrase(config, "en"),
        "fr": f"Les données sont résumées par la moyenne, l’écart-type (ET), l’erreur standard de la moyenne (ESM = ET/√n) et l’intervalle de confiance à {pct} % de la moyenne (loi t), ainsi que par la médiane et l’écart interquartile (quartiles par interpolation linéaire)."
        + _tech_phrase(config, "fr"),
    }
    return res


# ---------------------------------------------------------------- t-tests


def run_ttest(content: dict, config: dict) -> dict:
    variant = config.get("variant", "welch")
    titles = {
        "student": ("Unpaired t-test (Student)", "Test t non apparié (Student)"),
        "welch": ("Unpaired t-test (Welch)", "Test t non apparié (Welch)"),
        "paired": ("Paired t-test", "Test t apparié"),
    }
    if variant not in titles:
        raise AnalysisError([msg("unknown_analysis", kind=f"ttest/{variant}")])
    res = _result("ttest", titles[variant])
    level = float(config.get("ci_level", 0.95))
    alt = config.get("alternative", "two-sided")
    a, b = _prepare_two_groups(content, config, res)
    if variant == "paired":
        return _paired_t(res, a, b, alt, level, config)

    res["groups"] = [_group_summary(a, level), _group_summary(b, level)]
    errors = []
    for g in (a, b):
        if len(g.values) < 2:
            errors.append(msg("n_too_small", group=g.name, n=len(g.values), min=2))
    if errors:
        raise AnalysisError(errors)
    x, y = np.asarray(a.values), np.asarray(b.values)
    vx, vy = x.var(ddof=1), y.var(ddof=1)
    if vx == 0 and vy == 0:
        raise AnalysisError([msg("zero_variance_both")])
    if variant == "student":
        for g, v in ((a, vx), (b, vy)):
            if v == 0:
                res["warnings"].append(msg("zero_variance", group=g.name))
    nmin = min(len(x), len(y))
    if nmin < 5:
        res["warnings"].append(msg("small_n_warning", n=nmin))

    r = stats.ttest_ind(x, y, equal_var=(variant == "student"), alternative=alt)
    ci = r.confidence_interval(confidence_level=level)
    df = float(r.df)
    res["test"] = {
        "name": res["title"],
        "statistic_name": "t",
        "statistic": float(r.statistic),
        "df": df,
        "p": float(r.pvalue),
        "p_display": format_p(float(r.pvalue)),
        "alternative": alt,
    }
    res["estimate"] = {
        "name": {"en": f"Difference of means ({a.name} − {b.name})", "fr": f"Différence des moyennes ({a.name} − {b.name})"},
        "value": float(x.mean() - y.mean()),
        "ci": [float(ci.low), float(ci.high)],
        "ci_level": level,
    }
    if vx > 0 or vy > 0:
        res["effect_size"] = _cohen_d_independent(x, y, level)
    if variant == "student":
        res["assumptions"] = [
            {"en": "Observations are independent (one value per experimental unit).", "fr": "Les observations sont indépendantes (une valeur par unité expérimentale)."},
            {"en": "Each group is approximately normally distributed (or n is large).", "fr": "Chaque groupe suit approximativement une loi normale (ou n est grand)."},
            {"en": "Equal variances in both groups.", "fr": "Variances égales dans les deux groupes."},
        ]
    else:
        res["assumptions"] = [
            {"en": "Observations are independent (one value per experimental unit).", "fr": "Les observations sont indépendantes (une valeur par unité expérimentale)."},
            {"en": "Each group is approximately normally distributed (or n is large).", "fr": "Chaque groupe suit approximativement une loi normale (ou n est grand)."},
            {"en": "Variances may differ (Welch–Satterthwaite degrees of freedom).", "fr": "Les variances peuvent différer (degrés de liberté de Welch–Satterthwaite)."},
        ]
    _normality_diags(res, [a, b])
    bf = brown_forsythe(a.values, b.values)
    if bf:
        bf["name"] = {"en": "Brown–Forsythe test for equal variances", "fr": "Test de Brown–Forsythe d’égalité des variances"}
        res["diagnostics"].append(bf)
    if min(vx, vy) > 0 and max(vx, vy) / min(vx, vy) >= 4 and variant == "student":
        res["warnings"].append(msg("variance_ratio_large", ratio=f"{max(vx, vy) / min(vx, vy):.1f}"))
    res["comparisons"] = [{"group1": a.name, "group2": b.name, "p": float(r.pvalue), "p_display": format_p(float(r.pvalue))}]

    alt_en, alt_fr = ALT_TEXT[alt]
    pct = int(round(level * 100))
    name_en = "Student’s unpaired t-test (equal variances assumed)" if variant == "student" else "Welch’s unpaired t-test (unequal variances allowed)"
    name_fr = "un test t de Student non apparié (variances supposées égales)" if variant == "student" else "un test t de Welch non apparié (variances inégales autorisées)"
    res["method_text"] = {
        "en": f"{a.name} and {b.name} were compared with a {alt_en} {name_en}{_n_phrase(res['groups'], 'en')}.{_unit_phrase(config, 'en')}{_tech_phrase(config, 'en')} "
        f"We report the difference of means with its {pct}% confidence interval and Cohen’s d (pooled SD) with a {pct}% CI from the noncentral t distribution."
        + _footer("en"),
        "fr": f"{a.name} et {b.name} ont été comparés par {name_fr}, {alt_fr}{_n_phrase(res['groups'], 'fr')}.{_unit_phrase(config, 'fr')}{_tech_phrase(config, 'fr')} "
        f"Nous rapportons la différence des moyennes avec son intervalle de confiance à {pct} % et le d de Cohen (ET combiné) avec un IC à {pct} % issu de la loi t non centrale."
        + _footer("fr"),
    }
    res["result_text"] = {
        "en": f"t({fmt(df)}) = {fmt(float(r.statistic))}, p = {format_p(float(r.pvalue))}".replace("p = <", "p <"),
        "fr": f"t({fmt(df)}) = {fmt(float(r.statistic))}, p = {format_p(float(r.pvalue))}".replace("p = <", "p <"),
    }
    return res


def _normality_diags(res: dict, groups: list[Group], label_suffix: str = "") -> None:
    added = False
    for g in groups:
        sw = shapiro(g.values)
        if sw:
            sw["name"] = {"en": f"Shapiro–Wilk normality ({g.name}{label_suffix})", "fr": f"Normalité de Shapiro–Wilk ({g.name}{label_suffix})"}
            res["diagnostics"].append(sw)
            added = True
    if added:
        res["diagnostics_note"] = msg("normality_info")


def _paired_t(res: dict, a: Group, b: Group, alt: str, level: float, config: dict) -> dict:
    x, y, units, warns = pair_groups(a, b)
    res["warnings"].extend(warns)
    pa = Group(name=a.name, values=x, units=units)
    pb = Group(name=b.name, values=y, units=units)
    res["groups"] = [_group_summary(pa, level), _group_summary(pb, level)]
    n = len(x)
    if n < 2:
        raise AnalysisError([msg("n_pairs_too_small", n=n, min=2)])
    d = np.asarray(x) - np.asarray(y)
    if np.ptp(d) == 0:
        raise AnalysisError([msg("zero_variance_diff")])
    if n < 5:
        res["warnings"].append(msg("small_n_warning", n=n))
    r = stats.ttest_rel(x, y, alternative=alt)
    ci = r.confidence_interval(confidence_level=level)
    res["n_pairs"] = n
    res["pairs"] = [{"unit": u, "a": xa, "b": yb} for u, xa, yb in zip(units, x, y)]
    res["test"] = {
        "name": res["title"],
        "statistic_name": "t",
        "statistic": float(r.statistic),
        "df": float(r.df),
        "p": float(r.pvalue),
        "p_display": format_p(float(r.pvalue)),
        "alternative": alt,
    }
    res["estimate"] = {
        "name": {"en": f"Mean of differences ({a.name} − {b.name})", "fr": f"Moyenne des différences ({a.name} − {b.name})"},
        "value": float(d.mean()),
        "ci": [float(ci.low), float(ci.high)],
        "ci_level": level,
    }
    dz = d.mean() / d.std(ddof=1)
    nci = nct_ci(float(r.statistic), n - 1, level)
    res["effect_size"] = {
        "name": {"en": "Cohen's d_z (mean difference / SD of differences)", "fr": "d_z de Cohen (moyenne des différences / ET des différences)"},
        "symbol": "d_z",
        "value": float(dz),
        "ci": [nci[0] / math.sqrt(n), nci[1] / math.sqrt(n)] if nci else None,
        "ci_level": level,
        "ci_method": {"en": "noncentral t", "fr": "t non centrale"},
    }
    res["assumptions"] = [
        {"en": "Pairs are independent of each other.", "fr": "Les paires sont indépendantes entre elles."},
        {"en": "The paired differences are approximately normally distributed.", "fr": "Les différences appariées suivent approximativement une loi normale."},
    ]
    _normality_diags(res, [Group(name="differences" if True else "", values=list(d))])
    for dg in res["diagnostics"]:
        dg["name"] = {"en": "Shapiro–Wilk normality of differences", "fr": "Normalité de Shapiro–Wilk des différences"}
    res["comparisons"] = [{"group1": a.name, "group2": b.name, "p": float(r.pvalue), "p_display": format_p(float(r.pvalue)), "paired": True}]
    alt_en, alt_fr = ALT_TEXT[alt]
    pct = int(round(level * 100))
    res["method_text"] = {
        "en": f"{a.name} and {b.name} were compared with a {alt_en} paired t-test on {n} matched pairs.{_unit_phrase(config, 'en')}{_tech_phrase(config, 'en')} "
        f"We report the mean of the paired differences with its {pct}% confidence interval and Cohen’s d_z with a {pct}% CI from the noncentral t distribution." + _footer("en"),
        "fr": f"{a.name} et {b.name} ont été comparés par un test t apparié, {alt_fr}, sur {n} paires.{_unit_phrase(config, 'fr')}{_tech_phrase(config, 'fr')} "
        f"Nous rapportons la moyenne des différences appariées avec son intervalle de confiance à {pct} % et le d_z de Cohen avec un IC à {pct} % issu de la loi t non centrale." + _footer("fr"),
    }
    res["result_text"] = {k: f"t({fmt(float(r.df))}) = {fmt(float(r.statistic))}, p = {format_p(float(r.pvalue))}".replace("p = <", "p <") for k in ("en", "fr")}
    return res


# ---------------------------------------------------------------- rank tests


def _has_ties(v) -> bool:
    return len(np.unique(v)) < len(v)


def run_mannwhitney(content: dict, config: dict) -> dict:
    res = _result("mannwhitney", ("Mann–Whitney test", "Test de Mann–Whitney"))
    level = float(config.get("ci_level", 0.95))
    alt = config.get("alternative", "two-sided")
    a, b = _prepare_two_groups(content, config, res)
    res["groups"] = [_group_summary(a, level), _group_summary(b, level)]
    errors = [msg("n_too_small", group=g.name, n=len(g.values), min=1) for g in (a, b) if len(g.values) < 1]
    if errors:
        raise AnalysisError(errors)
    x, y = np.asarray(a.values), np.asarray(b.values)
    n1, n2 = len(x), len(y)
    if np.ptp(np.concatenate([x, y])) == 0:
        raise AnalysisError([msg("zero_variance_both")])
    ties = _has_ties(np.concatenate([x, y]))
    # Same rule as R's wilcox.test: exact if n < 50 in both groups and no ties.
    exact = n1 < 50 and n2 < 50 and not ties
    r = stats.mannwhitneyu(x, y, alternative=alt, method="exact" if exact else "asymptotic", use_continuity=True)
    if ties:
        res["warnings"].append(msg("ties_normal_approx"))
    if min(n1, n2) < 5:
        res["warnings"].append(msg("small_n_warning", n=min(n1, n2)))
    U = float(r.statistic)
    res["test"] = {
        "name": res["title"],
        "statistic_name": "U",
        "statistic": U,
        "df": None,
        "p": float(r.pvalue),
        "p_display": format_p(float(r.pvalue)),
        "alternative": alt,
        "method": "exact" if exact else "normal approximation with continuity correction",
    }
    res["effect_size"] = {
        "name": {"en": "Rank-biserial correlation", "fr": "Corrélation bisériale de rang"},
        "symbol": "r_rb",
        "value": 2 * U / (n1 * n2) - 1,
        "ci": None,
        "note": msg("ci_unavailable"),
    }
    res["estimate"] = {
        "name": {"en": f"Difference of medians ({a.name} − {b.name})", "fr": f"Différence des médianes ({a.name} − {b.name})"},
        "value": float(np.median(x) - np.median(y)),
        "ci": None,
    }
    res["assumptions"] = [
        {"en": "Observations are independent (one value per experimental unit).", "fr": "Les observations sont indépendantes (une valeur par unité expérimentale)."},
        {"en": "Tests whether values in one group tend to be larger than in the other (stochastic dominance). It is a test of medians only if both distributions have the same shape.",
         "fr": "Teste si les valeurs d’un groupe tendent à être plus grandes que dans l’autre (dominance stochastique). Ce n’est un test de médianes que si les deux distributions ont la même forme."},
    ]
    res["comparisons"] = [{"group1": a.name, "group2": b.name, "p": float(r.pvalue), "p_display": format_p(float(r.pvalue))}]
    alt_en, alt_fr = ALT_TEXT[alt]
    how_en = "exact p-value" if exact else "normal approximation with continuity correction"
    how_fr = "valeur de p exacte" if exact else "approximation normale avec correction de continuité"
    res["method_text"] = {
        "en": f"{a.name} and {b.name} were compared with a {alt_en} Mann–Whitney U test ({how_en}){_n_phrase(res['groups'], 'en')}.{_unit_phrase(config, 'en')}{_tech_phrase(config, 'en')} The effect size is the rank-biserial correlation." + _footer("en"),
        "fr": f"{a.name} et {b.name} ont été comparés par un test U de Mann–Whitney, {alt_fr} ({how_fr}){_n_phrase(res['groups'], 'fr')}.{_unit_phrase(config, 'fr')}{_tech_phrase(config, 'fr')} La taille d’effet est la corrélation bisériale de rang." + _footer("fr"),
    }
    res["result_text"] = {k: f"U = {fmt(U)}, p = {format_p(float(r.pvalue))}".replace("p = <", "p <") for k in ("en", "fr")}
    return res


def run_wilcoxon(content: dict, config: dict) -> dict:
    res = _result("wilcoxon", ("Wilcoxon matched-pairs signed-rank test", "Test des rangs signés de Wilcoxon (apparié)"))
    level = float(config.get("ci_level", 0.95))
    alt = config.get("alternative", "two-sided")
    a, b = _prepare_two_groups(content, config, res)
    x, y, units, warns = pair_groups(a, b)
    res["warnings"].extend(warns)
    res["groups"] = [_group_summary(Group(name=a.name, values=x), level), _group_summary(Group(name=b.name, values=y), level)]
    d = np.asarray(x) - np.asarray(y)
    nz = d[d != 0]
    zeros = int((d == 0).sum())
    if zeros:
        res["warnings"].append(msg("zeros_dropped", count=zeros))
    if len(nz) < 1:
        raise AnalysisError([msg("zero_variance_diff")])
    if len(d) < 5:
        res["warnings"].append(msg("small_n_warning", n=len(d)))
    ties = _has_ties(np.abs(nz))
    # Same rule as R: exact if n < 50 and no ties and no zeros
    exact = len(d) < 50 and not ties and zeros == 0
    ranks = stats.rankdata(np.abs(nz))
    v_plus = float(ranks[nz > 0].sum())
    v_minus = float(ranks[nz < 0].sum())
    r = stats.wilcoxon(x, y, zero_method="wilcox", correction=True, alternative=alt, method="exact" if exact else "approx")
    if not exact:
        res["warnings"].append(msg("ties_normal_approx"))
    res["n_pairs"] = len(d)
    res["pairs"] = [{"unit": u, "a": xa, "b": yb} for u, xa, yb in zip(units, x, y)]
    res["test"] = {
        "name": res["title"],
        "statistic_name": "V",  # sum of positive ranks, as reported by R
        "statistic": v_plus,
        "df": None,
        "p": float(r.pvalue),
        "p_display": format_p(float(r.pvalue)),
        "alternative": alt,
        "method": "exact" if exact else "normal approximation with continuity correction",
    }
    res["effect_size"] = {
        "name": {"en": "Matched-pairs rank-biserial correlation", "fr": "Corrélation bisériale de rang appariée"},
        "symbol": "r_rb",
        "value": (v_plus - v_minus) / (v_plus + v_minus),
        "ci": None,
        "note": msg("ci_unavailable"),
    }
    res["estimate"] = {
        "name": {"en": f"Median of differences ({a.name} − {b.name})", "fr": f"Médiane des différences ({a.name} − {b.name})"},
        "value": float(np.median(d)),
        "ci": None,
    }
    res["assumptions"] = [
        {"en": "Pairs are independent of each other.", "fr": "Les paires sont indépendantes entre elles."},
        {"en": "The distribution of differences is symmetric around its median.", "fr": "La distribution des différences est symétrique autour de sa médiane."},
    ]
    res["comparisons"] = [{"group1": a.name, "group2": b.name, "p": float(r.pvalue), "p_display": format_p(float(r.pvalue)), "paired": True}]
    alt_en, alt_fr = ALT_TEXT[alt]
    how_en = "exact p-value" if exact else "normal approximation with continuity correction"
    how_fr = "valeur de p exacte" if exact else "approximation normale avec correction de continuité"
    res["method_text"] = {
        "en": f"{a.name} and {b.name} were compared with a {alt_en} Wilcoxon matched-pairs signed-rank test ({how_en}) on {len(d)} pairs; zero differences were dropped.{_unit_phrase(config, 'en')}{_tech_phrase(config, 'en')} The effect size is the matched-pairs rank-biserial correlation." + _footer("en"),
        "fr": f"{a.name} et {b.name} ont été comparés par un test des rangs signés de Wilcoxon apparié, {alt_fr} ({how_fr}), sur {len(d)} paires ; les différences nulles ont été écartées.{_unit_phrase(config, 'fr')}{_tech_phrase(config, 'fr')} La taille d’effet est la corrélation bisériale de rang appariée." + _footer("fr"),
    }
    res["result_text"] = {k: f"V = {fmt(v_plus)}, p = {format_p(float(r.pvalue))}".replace("p = <", "p <") for k in ("en", "fr")}
    return res


# ---------------------------------------------------------------- XY


def _xy(content: dict, config: dict, res: dict):
    xc, yc = config.get("x"), config.get("y")
    cx, cy = col_by_id(content, xc), col_by_id(content, yc)
    if cx is None or cy is None:
        raise AnalysisError([msg("column_not_found", col=xc if cx is None else yc)])
    xs, ys, ids, n_missing, n_excl = extract_xy(content, xc, yc)
    if n_missing:
        res["warnings"].append(msg("missing_dropped", count=n_missing))
    if n_excl:
        res["warnings"].append(msg("excluded_dropped", count=n_excl))
    return cx["name"], cy["name"], np.asarray(xs), np.asarray(ys)


def run_correlation(content: dict, config: dict) -> dict:
    method = config.get("method", "pearson")
    titles = {"pearson": ("Pearson correlation", "Corrélation de Pearson"), "spearman": ("Spearman rank correlation", "Corrélation des rangs de Spearman")}
    res = _result("correlation", titles[method])
    level = float(config.get("ci_level", 0.95))
    alt = config.get("alternative", "two-sided")
    xn, yn, x, y = _xy(content, config, res)
    n = len(x)
    res["groups"] = [dict(describe(list(x), level), name=xn), dict(describe(list(y), level), name=yn)]
    if n < 3:
        raise AnalysisError([msg("n_pairs_too_small", n=n, min=3)])
    if np.ptp(x) == 0 or np.ptp(y) == 0:
        raise AnalysisError([msg("zero_variance", group=xn if np.ptp(x) == 0 else yn)])
    if method == "pearson":
        r = stats.pearsonr(x, y, alternative=alt)
        ci = r.confidence_interval(confidence_level=level) if n > 3 else None
        res["test"] = {"name": res["title"], "statistic_name": "t", "statistic": float(r.statistic * math.sqrt((n - 2) / (1 - r.statistic**2))) if abs(r.statistic) < 1 else None,
                       "df": n - 2, "p": float(r.pvalue), "p_display": format_p(float(r.pvalue)), "alternative": alt}
        res["effect_size"] = {"name": {"en": "Pearson r", "fr": "r de Pearson"}, "symbol": "r", "value": float(r.statistic),
                              "ci": [float(ci.low), float(ci.high)] if ci else None, "ci_level": level, "ci_method": {"en": "Fisher z", "fr": "z de Fisher"}}
        res["r_squared"] = float(r.statistic**2)
        res["assumptions"] = [
            {"en": "Pairs of observations are independent.", "fr": "Les paires d’observations sont indépendantes."},
            {"en": "The relationship is linear; X and Y are approximately bivariate normal (for the p-value and CI).", "fr": "La relation est linéaire ; X et Y suivent approximativement une loi normale bivariée (pour p et l’IC)."},
        ]
        method_en, method_fr = "Pearson’s product-moment correlation (CI by Fisher’s z transformation)", "la corrélation de Pearson (IC par transformation z de Fisher)"
        rt = f"r = {fmt(float(r.statistic))}, p = {format_p(float(r.pvalue))}"
    else:
        rho, _ = stats.spearmanr(x, y)
        # t approximation, as R's cor.test(method="spearman", exact=FALSE)
        tstat = rho * math.sqrt((n - 2) / (1 - rho**2)) if abs(rho) < 1 else math.inf * np.sign(rho)
        if alt == "two-sided":
            p = 2 * stats.t.sf(abs(tstat), n - 2)
        elif alt == "greater":
            p = stats.t.sf(tstat, n - 2)
        else:
            p = stats.t.cdf(tstat, n - 2)
        p = float(min(p, 1.0))
        res["test"] = {"name": res["title"], "statistic_name": "t", "statistic": float(tstat) if math.isfinite(tstat) else None, "df": n - 2,
                       "p": p, "p_display": format_p(p), "alternative": alt, "method": "t approximation"}
        res["effect_size"] = {"name": {"en": "Spearman ρ", "fr": "ρ de Spearman"}, "symbol": "ρ", "value": float(rho), "ci": None, "note": msg("ci_unavailable")}
        res["assumptions"] = [
            {"en": "Pairs of observations are independent.", "fr": "Les paires d’observations sont indépendantes."},
            {"en": "The relationship is monotonic.", "fr": "La relation est monotone."},
        ]
        method_en, method_fr = "Spearman’s rank correlation (p-value from the t approximation)", "la corrélation des rangs de Spearman (valeur de p par approximation t)"
        rt = f"ρ = {fmt(float(rho))}, p = {format_p(p)}"
    res["n"] = n
    alt_en, alt_fr = ALT_TEXT[alt]
    res["method_text"] = {
        "en": f"The association between {xn} and {yn} was assessed with {method_en}, {alt_en}, on n = {n} pairs." + _footer("en"),
        "fr": f"L’association entre {xn} et {yn} a été évaluée par {method_fr}, {alt_fr}, sur n = {n} paires." + _footer("fr"),
    }
    res["result_text"] = {k: rt.replace("p = <", "p <") for k in ("en", "fr")}
    return res


def run_linear_regression(content: dict, config: dict) -> dict:
    import statsmodels.api as sm

    res = _result("linear_regression", ("Simple linear regression", "Régression linéaire simple"))
    level = float(config.get("ci_level", 0.95))
    xn, yn, x, y = _xy(content, config, res)
    n = len(x)
    res["groups"] = [dict(describe(list(x), level), name=xn), dict(describe(list(y), level), name=yn)]
    if n < 3:
        raise AnalysisError([msg("n_pairs_too_small", n=n, min=3)])
    if np.ptp(x) == 0:
        raise AnalysisError([msg("x_zero_variance")])
    X = sm.add_constant(x)
    fit = sm.OLS(y, X).fit()
    ci = fit.conf_int(alpha=1 - level)
    if fit.ssr <= 1e-24 * max(1.0, float(np.sum((y - y.mean()) ** 2))):
        res["warnings"].append(msg("perfect_fit"))
    if n < 8:
        res["warnings"].append(msg("small_n_warning", n=n))
    coefs = []
    for i, (nm_en, nm_fr) in enumerate([("Intercept", "Ordonnée à l’origine"), ("Slope", "Pente")]):
        coefs.append({
            "name": {"en": nm_en, "fr": nm_fr},
            "estimate": float(fit.params[i]),
            "se": float(fit.bse[i]),
            "t": float(fit.tvalues[i]),
            "df": float(fit.df_resid),
            "p": float(fit.pvalues[i]),
            "p_display": format_p(float(fit.pvalues[i])),
            "ci": [float(ci[i][0]), float(ci[i][1])],
            "ci_level": level,
        })
    res["coefficients"] = coefs
    res["n"] = n
    res["fit"] = {
        "r_squared": float(fit.rsquared),
        "adj_r_squared": float(fit.rsquared_adj),
        "sigma": float(math.sqrt(fit.scale)),
        "f": float(fit.fvalue) if fit.fvalue is not None else None,
        "f_df": [1, float(fit.df_resid)],
        "f_p": float(fit.f_pvalue) if fit.f_pvalue is not None else None,
        "x_intercept": float(-fit.params[0] / fit.params[1]) if fit.params[1] != 0 else None,
    }
    slope = coefs[1]
    res["test"] = {"name": {"en": "Slope ≠ 0 (t-test)", "fr": "Pente ≠ 0 (test t)"}, "statistic_name": "t", "statistic": slope["t"], "df": slope["df"],
                   "p": slope["p"], "p_display": slope["p_display"], "alternative": "two-sided"}
    res["effect_size"] = {"name": {"en": "R²", "fr": "R²"}, "symbol": "R²", "value": float(fit.rsquared), "ci": None}
    resid = fit.resid
    res["residuals"] = [{"x": float(a), "fitted": float(f), "residual": float(r)} for a, f, r in zip(x, fit.fittedvalues, resid)]
    sw = shapiro(list(resid))
    if sw:
        sw["name"] = {"en": "Shapiro–Wilk normality of residuals", "fr": "Normalité des résidus (Shapiro–Wilk)"}
        res["diagnostics"].append(sw)
        res["diagnostics_note"] = msg("normality_info")
    res["assumptions"] = [
        {"en": "The relationship between X and Y is linear.", "fr": "La relation entre X et Y est linéaire."},
        {"en": "Residuals are independent, with constant variance, and approximately normal.", "fr": "Les résidus sont indépendants, de variance constante et approximativement normaux."},
        {"en": "X is measured without substantial error.", "fr": "X est mesuré sans erreur importante."},
    ]
    pct = int(round(level * 100))
    res["method_text"] = {
        "en": f"{yn} was modelled as a linear function of {xn} by ordinary least squares (n = {n}). We report the slope and intercept with {pct}% confidence intervals, the t-test of the slope against zero, and R²." + _footer("en"),
        "fr": f"{yn} a été modélisé comme fonction linéaire de {xn} par moindres carrés ordinaires (n = {n}). Nous rapportons la pente et l’ordonnée à l’origine avec leurs intervalles de confiance à {pct} %, le test t de la pente contre zéro et le R²." + _footer("fr"),
    }
    res["result_text"] = {k: f"slope = {fmt(slope['estimate'])}, t({fmt(slope['df'])}) = {fmt(slope['t'])}, p = {slope['p_display']}, R² = {fmt(float(fit.rsquared))}".replace("p = <", "p <") for k in ("en",)}
    res["result_text"]["fr"] = res["result_text"]["en"].replace("slope", "pente")
    return res
