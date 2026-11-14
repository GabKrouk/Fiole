"""Bilingual server messages (warnings, errors, assumptions). Each message is
returned with both languages so the UI can switch language without re-running."""
from __future__ import annotations

MESSAGES: dict[str, tuple[str, str]] = {
    # data / design
    "design_incomplete_long": (
        "Long format needs a group column and a value column. Set them in the data table's design.",
        "Le format long nécessite une colonne de groupe et une colonne de valeurs. Définissez-les dans le plan du tableau.",
    ),
    "technical_needs_subject": (
        "Averaging technical replicates needs a subject / experimental unit column.",
        "Moyenner les répétitions techniques nécessite une colonne sujet / unité expérimentale.",
    ),
    "pairing_needs_subject": (
        "A paired test needs to know which values belong together. Set a subject column (long format), or keep paired values on the same row (wide format).",
        "Un test apparié doit savoir quelles valeurs vont ensemble. Définissez une colonne sujet (format long) ou gardez les valeurs appariées sur la même ligne (format large).",
    ),
    "pairing_duplicate_unit": (
        "Unit “{unit}” appears more than once in group “{group}”. Average technical replicates or fix the data before a paired test.",
        "L’unité « {unit} » apparaît plusieurs fois dans le groupe « {group} ». Moyennez les répétitions techniques ou corrigez les données avant un test apparié.",
    ),
    "pairing_unmatched": (
        "{count} unit(s) have no partner in the other group and were left out: {units}.",
        "{count} unité(s) sans partenaire dans l’autre groupe ont été écartées : {units}.",
    ),
    "group_not_found": ("Group “{group}” was not found in the data.", "Le groupe « {group} » est introuvable dans les données."),
    "need_two_groups": ("Choose exactly two groups.", "Choisissez exactement deux groupes."),
    "column_not_found": ("Column not found: {col}.", "Colonne introuvable : {col}."),
    "missing_dropped": (
        "{count} missing value(s) were left out (not replaced).",
        "{count} valeur(s) manquante(s) ont été écartées (non remplacées).",
    ),
    "excluded_dropped": (
        "{count} excluded value(s) were left out (exclusions are listed in the data table).",
        "{count} valeur(s) exclue(s) ont été écartées (exclusions listées dans le tableau de données).",
    ),
    "technical_averaged": (
        "Technical replicates were averaged within each experimental unit: {raw} values → {units} units. n is the number of units.",
        "Les répétitions techniques ont été moyennées par unité expérimentale : {raw} valeurs → {units} unités. n = nombre d’unités.",
    ),
    # stats problems
    "n_too_small": (
        "Group “{group}” has n = {n}; at least {min} values are needed.",
        "Le groupe « {group} » a n = {n} ; il faut au moins {min} valeurs.",
    ),
    "n_pairs_too_small": ("Only {n} complete pair(s); at least {min} are needed.", "Seulement {n} paire(s) complète(s) ; il en faut au moins {min}."),
    "zero_variance": (
        "Group “{group}” has zero variance (all values equal). The test statistic cannot be computed.",
        "Le groupe « {group} » a une variance nulle (valeurs toutes égales). La statistique ne peut pas être calculée.",
    ),
    "zero_variance_both": (
        "Both groups have zero variance. The test cannot be computed.",
        "Les deux groupes ont une variance nulle. Le test ne peut pas être calculé.",
    ),
    "zero_variance_diff": (
        "All paired differences are equal. The test cannot be computed.",
        "Toutes les différences appariées sont égales. Le test ne peut pas être calculé.",
    ),
    "small_n_warning": (
        "Small sample (n = {n} in at least one group): assumptions cannot be checked reliably and power is low.",
        "Petit effectif (n = {n} dans au moins un groupe) : les hypothèses ne peuvent pas être vérifiées de façon fiable et la puissance est faible.",
    ),
    "ties_normal_approx": (
        "Ties or zeros are present: the p-value uses the normal approximation with continuity correction (exact p not available).",
        "Présence d’ex-aequo ou de zéros : la valeur de p utilise l’approximation normale avec correction de continuité (p exact indisponible).",
    ),
    "zeros_dropped": (
        "{count} zero difference(s) were dropped (Wilcoxon convention).",
        "{count} différence(s) nulle(s) écartée(s) (convention de Wilcoxon).",
    ),
    "x_zero_variance": (
        "X has zero variance: the regression slope cannot be estimated.",
        "X a une variance nulle : la pente de régression ne peut pas être estimée.",
    ),
    "perfect_fit": (
        "The points lie exactly on a line: standard errors are zero and p-values are not meaningful.",
        "Les points sont exactement alignés : les erreurs standard sont nulles et les valeurs de p n’ont pas de sens.",
    ),
    "variance_ratio_large": (
        "The variances differ by a factor of {ratio}. Welch’s test does not assume equal variances.",
        "Les variances diffèrent d’un facteur {ratio}. Le test de Welch ne suppose pas l’égalité des variances.",
    ),
    "normality_info": (
        "Normality tests are shown for information only and were not used to choose the test. With small samples they have little power; with large samples they flag trivial departures.",
        "Les tests de normalité sont indiqués à titre informatif et n’ont pas servi à choisir le test. Avec de petits effectifs ils sont peu puissants ; avec de grands effectifs ils détectent des écarts négligeables.",
    ),
    "ci_unavailable": ("Confidence interval not available for this quantity.", "Intervalle de confiance indisponible pour cette quantité."),
    "unknown_analysis": ("Unknown analysis type: {kind}.", "Type d’analyse inconnu : {kind}."),
    "not_implemented": (
        "This analysis is not available yet.",
        "Cette analyse n’est pas encore disponible.",
    ),
    "spearman_ties": (
        "Ties are present: the Spearman p-value uses the t approximation.",
        "Présence d’ex-aequo : la valeur de p de Spearman utilise l’approximation t.",
    ),
}


def msg(code: str, **params) -> dict:
    en, fr = MESSAGES[code]
    return {"code": code, "en": en.format(**params), "fr": fr.format(**params), "params": {k: str(v) for k, v in params.items()}}
