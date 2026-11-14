"""Analysis registry. New analyses (ANOVA, mixed models, survival…) register here
with their design questions; the UI reads the registry to build forms."""
from __future__ import annotations

from ..data import DataError
from ..i18n import msg
from .analyses import (
    ENGINE,
    AnalysisError,
    run_correlation,
    run_descriptive,
    run_linear_regression,
    run_mannwhitney,
    run_ttest,
    run_wilcoxon,
    run_pairwise,
)
from .core import clean

# input: "groups" (pick groups from the table design) or "xy" (pick two numeric columns)
REGISTRY: dict[str, dict] = {
    "descriptive": {"run": run_descriptive, "input": "groups", "min_groups": 1, "max_groups": None, "available": True,
                    "label": {"en": "Descriptive statistics", "fr": "Statistiques descriptives"}, "family": "describe"},
    "ttest": {"run": run_ttest, "input": "groups", "min_groups": 2, "max_groups": 2, "available": True,
              "label": {"en": "t-test (Student, Welch, paired)", "fr": "Test t (Student, Welch, apparié)"}, "family": "compare2",
              "options": {"variant": ["welch", "student", "paired"]}},
    "mannwhitney": {"run": run_mannwhitney, "input": "groups", "min_groups": 2, "max_groups": 2, "available": True,
                    "label": {"en": "Mann–Whitney (unpaired, rank-based)", "fr": "Mann–Whitney (non apparié, rangs)"}, "family": "compare2"},
    "wilcoxon": {"run": run_wilcoxon, "input": "groups", "min_groups": 2, "max_groups": 2, "available": True, "paired": True,
                 "label": {"en": "Wilcoxon signed-rank (paired)", "fr": "Wilcoxon des rangs signés (apparié)"}, "family": "compare2"},
    "pairwise": {"run": run_pairwise, "input": "groups", "min_groups": 2, "max_groups": None, "available": True,
                 "label": {"en": "Pairwise comparisons (all pairs or vs control, Holm/Bonferroni)", "fr": "Comparaisons deux à deux (toutes paires ou vs contrôle, Holm/Bonferroni)"},
                 "family": "compareN", "options": {"test": ["welch", "student", "paired", "mannwhitney", "wilcoxon"], "correction": ["holm", "bonferroni", "none"], "mode": ["all", "control"]}},
    "correlation": {"run": run_correlation, "input": "xy", "available": True,
                    "label": {"en": "Correlation (Pearson, Spearman)", "fr": "Corrélation (Pearson, Spearman)"}, "family": "xy",
                    "options": {"method": ["pearson", "spearman"]}},
    "linear_regression": {"run": run_linear_regression, "input": "xy", "available": True,
                          "label": {"en": "Simple linear regression", "fr": "Régression linéaire simple"}, "family": "xy"},
    # Planned; shown as unavailable in the UI.
    "anova1": {"available": False, "label": {"en": "One-way ANOVA + multiple comparisons", "fr": "ANOVA à un facteur + comparaisons multiples"}, "family": "compareN"},
    "kruskal": {"available": False, "label": {"en": "Kruskal–Wallis + Dunn", "fr": "Kruskal–Wallis + Dunn"}, "family": "compareN"},
    "dose_response": {"available": False, "label": {"en": "Dose–response (4PL, IC50/EC50)", "fr": "Dose–réponse (4PL, CI50/CE50)"}, "family": "xy"},
    "anova2": {"available": False, "label": {"en": "Two-way ANOVA", "fr": "ANOVA à deux facteurs"}, "family": "compareN"},
    "rm_anova": {"available": False, "label": {"en": "Repeated-measures ANOVA", "fr": "ANOVA à mesures répétées"}, "family": "compareN"},
    "mixed": {"available": False, "label": {"en": "Mixed-effects models", "fr": "Modèles mixtes"}, "family": "compareN"},
    "survival": {"available": False, "label": {"en": "Survival (Kaplan–Meier, log-rank)", "fr": "Survie (Kaplan–Meier, log-rank)"}, "family": "survival"},
}


def catalog() -> list[dict]:
    return [{"kind": k, **{kk: vv for kk, vv in v.items() if kk != "run"}} for k, v in REGISTRY.items()]


def run_analysis(kind: str, content: dict, config: dict) -> dict:
    spec = REGISTRY.get(kind)
    if spec is None:
        return clean({"kind": kind, "status": "error", "errors": [msg("unknown_analysis", kind=kind)], "warnings": [], "engine": ENGINE})
    if not spec.get("available"):
        return clean({"kind": kind, "status": "error", "errors": [msg("not_implemented")], "warnings": [], "engine": ENGINE})
    try:
        return clean(spec["run"](content, config))
    except AnalysisError as e:
        return clean({"kind": kind, "status": "error", "errors": e.messages, "warnings": [], "engine": ENGINE})
    except DataError as e:
        return clean({"kind": kind, "status": "error", "errors": [e.message], "warnings": [], "engine": ENGINE})
