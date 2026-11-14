import { createContext, useContext, useState, ReactNode, useCallback } from "react";
import type { Bi } from "./api";

export type Lang = "fr" | "en";

const D = {
  appTagline: { fr: "Statistiques et figures pour la biologie", en: "Statistics and figures for biology" },
  signIn: { fr: "Se connecter", en: "Sign in" },
  signUp: { fr: "Créer un compte", en: "Create account" },
  signOut: { fr: "Se déconnecter", en: "Sign out" },
  email: { fr: "Adresse email", en: "Email address" },
  password: { fr: "Mot de passe (8 caractères min.)", en: "Password (8 characters min.)" },
  displayName: { fr: "Nom affiché", en: "Display name" },
  noAccount: { fr: "Pas encore de compte ?", en: "No account yet?" },
  haveAccount: { fr: "Déjà un compte ?", en: "Already have an account?" },
  badCredentials: { fr: "Email ou mot de passe incorrect.", en: "Wrong email or password." },
  emailTaken: { fr: "Un compte existe déjà avec cet email.", en: "An account already exists for this email." },
  myProjects: { fr: "Mes projets", en: "My projects" },
  sharedWithMe: { fr: "Partagés avec moi", en: "Shared with me" },
  recent: { fr: "Récents", en: "Recent" },
  newProject: { fr: "Nouveau projet", en: "New project" },
  importArchive: { fr: "Importer une archive", en: "Import archive" },
  projectName: { fr: "Nom du projet", en: "Project name" },
  create: { fr: "Créer", en: "Create" },
  cancel: { fr: "Annuler", en: "Cancel" },
  save: { fr: "Enregistrer", en: "Save" },
  delete: { fr: "Supprimer", en: "Delete" },
  close: { fr: "Fermer", en: "Close" },
  noProjects: { fr: "Aucun projet pour l’instant. Créez-en un pour commencer.", en: "No projects yet. Create one to get started." },
  sharedEmpty: {
    fr: "Aucun projet partagé avec vous pour l’instant. Quand quelqu’un vous ajoute à un projet (ou que vous ouvrez un lien de partage), il apparaît ici.",
    en: "Nothing shared with you yet. When someone adds you to a project (or you open a share link), it appears here.",
  },
  recentEmpty: { fr: "Aucun projet ouvert récemment.", en: "No recently opened projects." },
  updated: { fr: "Modifié", en: "Updated" },
  owner: { fr: "Propriétaire", en: "Owner" },
  data: { fr: "Données", en: "Data" },
  analyses: { fr: "Analyses", en: "Analyses" },
  figures: { fr: "Figures", en: "Figures" },
  share: { fr: "Partager", en: "Share" },
  comingSoon: { fr: "Bientôt disponible", en: "Coming soon" },
  shareSoon: {
    fr: "Le partage (rôles, invitations, lien) arrive dans la prochaine version. Le projet est déjà prêt : son adresse est stable.",
    en: "Sharing (roles, invitations, link) arrives in the next version. The project is ready for it: its address is stable.",
  },
  copyLink: { fr: "Copier le lien", en: "Copy link" },
  copied: { fr: "Copié", en: "Copied" },
  addData: { fr: "Nouveau tableau", en: "New table" },
  importData: { fr: "Importer / coller", en: "Import / paste" },
  emptyTable: { fr: "Tableau vide", en: "Empty table" },
  newAnalysis: { fr: "Nouvelle analyse", en: "New analysis" },
  newFigure: { fr: "Nouvelle figure", en: "New figure" },
  nothingYet: { fr: "Rien pour l’instant.", en: "Nothing yet." },
  saved: { fr: "Enregistré", en: "Saved" },
  saving: { fr: "Enregistrement…", en: "Saving…" },
  unsaved: { fr: "Modifications non enregistrées", en: "Unsaved changes" },
  conflict: {
    fr: "Ce tableau a été modifié ailleurs (autre onglet). Rechargez pour récupérer la dernière version ; vos dernières modifications non enregistrées seront perdues.",
    en: "This table was changed elsewhere (another tab). Reload to get the latest version; your last unsaved changes will be lost.",
  },
  reload: { fr: "Recharger", en: "Reload" },
  stale: { fr: "À recalculer", en: "Needs re-run" },
  upToDate: { fr: "À jour", en: "Up to date" },
  neverRun: { fr: "Jamais lancée", en: "Not run yet" },
  staleExplain: {
    fr: "Les données ou les réglages ont changé depuis le dernier calcul.",
    en: "The data or the settings changed since the last run.",
  },
  run: { fr: "Lancer l’analyse", en: "Run analysis" },
  rerun: { fr: "Relancer", en: "Re-run" },
  // Data grid
  addRow: { fr: "+ Ligne", en: "+ Row" },
  addColumn: { fr: "+ Colonne", en: "+ Column" },
  rows: { fr: "lignes", en: "rows" },
  columns: { fr: "colonnes", en: "columns" },
  numeric: { fr: "Numérique", en: "Numeric" },
  text: { fr: "Texte", en: "Text" },
  rename: { fr: "Renommer", en: "Rename" },
  deleteColumn: { fr: "Supprimer la colonne", en: "Delete column" },
  deleteRow: { fr: "Supprimer la ligne", en: "Delete row" },
  exclude: { fr: "Exclure", en: "Exclude" },
  include: { fr: "Réintégrer", en: "Include again" },
  excludeReason: { fr: "Motif de l’exclusion (obligatoire)", en: "Reason for exclusion (required)" },
  exclusions: { fr: "Exclusions", en: "Exclusions" },
  exclusionsHelp: {
    fr: "Les lignes exclues restent dans le tableau, barrées, et ne sont pas utilisées par les analyses ni les figures. Chaque exclusion est réversible.",
    en: "Excluded rows stay in the table, struck through, and are not used by analyses or figures. Every exclusion can be undone.",
  },
  missingHelp: {
    fr: "Cellule vide = valeur manquante. Elle n’est jamais remplacée par zéro.",
    en: "Empty cell = missing value. It is never replaced by zero.",
  },
  pasteHelp: {
    fr: "Astuce : collez directement depuis Excel avec Ctrl/Cmd+V dans une cellule.",
    en: "Tip: paste straight from Excel with Ctrl/Cmd+V into a cell.",
  },
  invalidNumber: { fr: "Valeur non numérique", en: "Not a number" },
  versionHistory: { fr: "Historique des versions", en: "Version history" },
  version: { fr: "Version", en: "Version" },
  exportCsv: { fr: "Exporter CSV", en: "Export CSV" },
  transformations: { fr: "Transformations (log, normalisation…)", en: "Transformations (log, normalisation…)" },
  // design
  design: { fr: "Plan expérimental", en: "Experimental design" },
  layout: { fr: "Format", en: "Layout" },
  wide: { fr: "Large : une colonne par groupe", en: "Wide: one column per group" },
  long: { fr: "Long : une ligne par observation", en: "Long: one row per observation" },
  groupColumns: { fr: "Colonnes = groupes", en: "Columns = groups" },
  groupCol: { fr: "Colonne des groupes", en: "Group column" },
  valueCol: { fr: "Colonne des valeurs", en: "Value column" },
  subjectCol: { fr: "Sujet / unité expérimentale (répétition biologique, appariement)", en: "Subject / experimental unit (biological replicate, pairing)" },
  technicalCol: { fr: "Répétition technique", en: "Technical replicate" },
  timeCol: { fr: "Temps", en: "Time" },
  conditionCol: { fr: "Condition", en: "Condition" },
  none: { fr: "— aucune —", en: "— none —" },
  wideSubjectHelp: {
    fr: "En format large, les valeurs d’une même ligne sont considérées comme appariées (même sujet).",
    en: "In wide layout, values on the same row are treated as paired (same subject).",
  },
  // import
  importTitle: { fr: "Importer des données", en: "Import data" },
  chooseFile: { fr: "Fichier CSV, TSV ou Excel (.xlsx)", en: "CSV, TSV or Excel (.xlsx) file" },
  orPaste: { fr: "…ou collez vos données (depuis Excel, un tableur ou un texte)", en: "…or paste your data (from Excel, a spreadsheet or text)" },
  preview: { fr: "Aperçu", en: "Preview" },
  delimiter: { fr: "Séparateur", en: "Delimiter" },
  decimal: { fr: "Décimale", en: "Decimal" },
  header: { fr: "Première ligne = noms de colonnes", en: "First row = column names" },
  sheet: { fr: "Feuille", en: "Sheet" },
  auto: { fr: "auto", en: "auto" },
  detected: { fr: "détecté", en: "detected" },
  missingFound: { fr: "valeur(s) manquante(s) détectée(s), conservées comme manquantes", en: "missing value(s) found, kept as missing" },
  confirmImport: { fr: "Valider l’import", en: "Confirm import" },
  tableName: { fr: "Nom du tableau", en: "Table name" },
  rawKept: { fr: "Le fichier d’origine est conservé tel quel.", en: "The original file is kept unchanged." },
  // analysis
  analysisType: { fr: "Type d’analyse", en: "Analysis type" },
  table: { fr: "Tableau", en: "Table" },
  groupsToCompare: { fr: "Groupes", en: "Groups" },
  variant: { fr: "Variante", en: "Variant" },
  welch: { fr: "Welch (variances inégales autorisées) — recommandé", en: "Welch (unequal variances allowed) — recommended" },
  student: { fr: "Student (variances supposées égales)", en: "Student (equal variances assumed)" },
  paired: { fr: "Apparié", en: "Paired" },
  alternative: { fr: "Hypothèse alternative", en: "Alternative hypothesis" },
  twoSided: { fr: "Bilatérale", en: "Two-sided" },
  less: { fr: "Unilatérale : groupe 1 < groupe 2", en: "One-sided: group 1 < group 2" },
  greater: { fr: "Unilatérale : groupe 1 > groupe 2", en: "One-sided: group 1 > group 2" },
  ciLevel: { fr: "Niveau de confiance", en: "Confidence level" },
  designQuestions: { fr: "Questions sur le plan expérimental", en: "Questions about the experimental design" },
  independentOrPaired: { fr: "Les groupes sont-ils indépendants ou appariés ?", en: "Are the groups independent or paired?" },
  independent: { fr: "Indépendants (sujets différents)", en: "Independent (different subjects)" },
  pairedQ: { fr: "Appariés (mêmes sujets, avant/après…)", en: "Paired (same subjects, before/after…)" },
  expUnit: { fr: "Unité expérimentale (ex. souris, culture, patient)", en: "Experimental unit (e.g. mouse, culture, patient)" },
  avgTech: { fr: "Moyenner les répétitions techniques par unité expérimentale avant le test", en: "Average technical replicates within each experimental unit before testing" },
  avgTechHelp: {
    fr: "Évite la pseudo-réplication : n devient le nombre d’unités expérimentales. Nécessite une colonne sujet.",
    en: "Avoids pseudo-replication: n becomes the number of experimental units. Needs a subject column.",
  },
  pairedMismatch: {
    fr: "Ce test est apparié mais vous avez indiqué des groupes indépendants (ou l’inverse). Choisissez le test adapté.",
    en: "This test is paired but you said the groups are independent (or the other way round). Pick the matching test.",
  },
  xColumn: { fr: "Colonne X", en: "X column" },
  yColumn: { fr: "Colonne Y", en: "Y column" },
  method: { fr: "Méthode", en: "Method" },
  results: { fr: "Résultats", en: "Results" },
  testUsed: { fr: "Test", en: "Test" },
  statistic: { fr: "Statistique", en: "Statistic" },
  df: { fr: "ddl", en: "df" },
  pValue: { fr: "Valeur de p", en: "p-value" },
  effectSize: { fr: "Taille d’effet", en: "Effect size" },
  estimate: { fr: "Estimation", en: "Estimate" },
  assumptions: { fr: "Hypothèses du test", en: "Test assumptions" },
  diagnostics: { fr: "Diagnostics", en: "Diagnostics" },
  warnings: { fr: "Avertissements", en: "Warnings" },
  errors: { fr: "Calcul impossible", en: "Cannot compute" },
  methodsText: { fr: "Texte de méthode (réutilisable)", en: "Methods text (reusable)" },
  copy: { fr: "Copier", en: "Copy" },
  computedOn: { fr: "Calculé sur la version", en: "Computed on version" },
  ofTable: { fr: "du tableau", en: "of table" },
  descriptive: { fr: "Statistiques descriptives", en: "Descriptive statistics" },
  group: { fr: "Groupe", en: "Group" },
  missing: { fr: "manquantes", en: "missing" },
  excluded: { fr: "exclues", en: "excluded" },
  exportResults: { fr: "Exporter tous les résultats (CSV)", en: "Export all results (CSV)" },
  exportArchive: { fr: "Archive du projet", en: "Project archive" },
  reportSoon: { fr: "Rapport complet (méthodes, résultats, figures)", en: "Full report (methods, results, figures)" },
  // figures
  figureType: { fr: "Type de figure", en: "Figure type" },
  scatter_groups: { fr: "Points par groupe", en: "Grouped scatter" },
  bar: { fr: "Barres + points", en: "Bars + points" },
  box: { fr: "Boîtes à moustaches", en: "Box plot" },
  violin: { fr: "Violons", en: "Violin" },
  xy: { fr: "Nuage XY + régression", en: "XY scatter + regression" },
  line: { fr: "Courbe / série temporelle", en: "Line / time series" },
  doseResponse: { fr: "Dose–réponse", en: "Dose–response" },
  multiPanel: { fr: "Composition multi-panneaux (A, B, C…)", en: "Multi-panel layout (A, B, C…)" },
  titles: { fr: "Titres et axes", en: "Titles and axes" },
  title: { fr: "Titre", en: "Title" },
  xLabel: { fr: "Titre de l’axe X", en: "X axis title" },
  yLabel: { fr: "Titre de l’axe Y", en: "Y axis title" },
  unit: { fr: "Unité", en: "Unit" },
  size: { fr: "Dimensions finales", en: "Final size" },
  width: { fr: "Largeur", en: "Width" },
  height: { fr: "Hauteur", en: "Height" },
  style: { fr: "Style", en: "Style" },
  font: { fr: "Police", en: "Font" },
  fontSize: { fr: "Taille du texte (pt)", en: "Text size (pt)" },
  titleSize: { fr: "Taille du titre (pt)", en: "Title size (pt)" },
  lineWidth: { fr: "Épaisseur des traits (pt)", en: "Line width (pt)" },
  pointSize: { fr: "Taille des points (pt)", en: "Point size (pt)" },
  pointShape: { fr: "Symbole", en: "Symbol" },
  palette: { fr: "Palette (adaptée au daltonisme)", en: "Palette (colour-blind safe)" },
  groupsOrder: { fr: "Groupes : ordre, noms et couleurs", en: "Groups: order, names and colours" },
  axes: { fr: "Échelles", en: "Scales" },
  yMin: { fr: "Y min", en: "Y min" },
  yMax: { fr: "Y max", en: "Y max" },
  xMin: { fr: "X min", en: "X min" },
  xMax: { fr: "X max", en: "X max" },
  scaleLinear: { fr: "Linéaire", en: "Linear" },
  scaleLog: { fr: "Logarithmique (log10)", en: "Logarithmic (log10)" },
  yScale: { fr: "Échelle Y", en: "Y scale" },
  xScale: { fr: "Échelle X", en: "X scale" },
  summary: { fr: "Résumé statistique affiché", en: "Summary shown" },
  center: { fr: "Tendance centrale", en: "Centre" },
  mean: { fr: "Moyenne", en: "Mean" },
  median: { fr: "Médiane", en: "Median" },
  errorBars: { fr: "Barres d’erreur", en: "Error bars" },
  sd: { fr: "Écart-type (ET) : dispersion des données", en: "SD: spread of the data" },
  sem: { fr: "Erreur standard (ESM = ET/√n) : précision de la moyenne", en: "SEM (SD/√n): precision of the mean" },
  ci95: { fr: "IC 95 % de la moyenne (loi t)", en: "95% CI of the mean (t distribution)" },
  iqr: { fr: "Écart interquartile (Q1–Q3)", en: "Interquartile range (Q1–Q3)" },
  range: { fr: "Étendue (min–max)", en: "Range (min–max)" },
  noError: { fr: "Aucune", en: "None" },
  showPoints: { fr: "Afficher les points individuels", en: "Show individual points" },
  jitter: { fr: "Dispersion horizontale des points", en: "Horizontal point spread" },
  regressionLine: { fr: "Droite de régression", en: "Regression line" },
  annotations: { fr: "Annotations", en: "Annotations" },
  addPValue: { fr: "Ajouter une valeur de p (depuis une analyse enregistrée)", en: "Add a p-value (from a saved analysis)" },
  pFormat: { fr: "Format", en: "Format" },
  pExact: { fr: "p = 0,012", en: "p = 0.012" },
  pStars: { fr: "Étoiles (*, **, ***)", en: "Stars (*, **, ***)" },
  addText: { fr: "Ajouter un texte manuel", en: "Add manual text" },
  manualTag: { fr: "manuel", en: "manual" },
  manualHelp: {
    fr: "Les textes manuels sont marqués comme tels dans la figure exportée et la légende.",
    en: "Manual text is marked as such in the exported figure and the caption.",
  },
  pOnlyFromAnalysis: {
    fr: "Les valeurs de p proviennent uniquement d’analyses enregistrées ; elles ne peuvent pas être saisies à la main.",
    en: "p-values only come from saved analyses; they cannot be typed in by hand.",
  },
  noAnalysisForP: { fr: "Aucune analyse à deux groupes lancée sur ce tableau.", en: "No two-group analysis has been run on this table." },
  export: { fr: "Exporter", en: "Export" },
  background: { fr: "Fond", en: "Background" },
  white: { fr: "Blanc", en: "White" },
  transparent: { fr: "Transparent", en: "Transparent" },
  dpi: { fr: "Résolution PNG (dpi)", en: "PNG resolution (dpi)" },
  caption: { fr: "Légende proposée", en: "Suggested caption" },
  previewNote: { fr: "Aperçu à l’échelle : identique à l’export.", en: "Scaled preview: identical to the export." },
  pixels: { fr: "pixels", en: "pixels" },
  legend: { fr: "Légende des séries", en: "Series legend" },
  yColumns: { fr: "Colonnes Y (séries)", en: "Y columns (series)" },
  confirmDelete: { fr: "Supprimer définitivement ?", en: "Delete permanently?" },
  lang: { fr: "English", en: "Français" },
  dashboard: { fr: "Tableau de bord", en: "Dashboard" },
  unavailableTitle: { fr: "Fonctions à venir", en: "Coming features" },
  duplicate: { fr: "Dupliquer le projet", en: "Duplicate project" },
  comments: { fr: "Commentaires", en: "Comments" },
  selectSomething: { fr: "Sélectionnez un élément à gauche, ou créez-en un.", en: "Select an item on the left, or create one." },
  noData: { fr: "Commencez par importer ou coller des données.", en: "Start by importing or pasting data." },
  steps: {
    fr: "1. Importez vos données · 2. Lancez une analyse · 3. Créez et exportez une figure",
    en: "1. Import your data · 2. Run an analysis · 3. Build and export a figure",
  },
  error: { fr: "Erreur", en: "Error" },
  groupColPick: { fr: "Groupes = modalités de la colonne", en: "Groups = levels of column" },
  valueColPick: { fr: "Valeurs (Y)", en: "Values (Y)" },
  useTableDesign: { fr: "— selon le plan du tableau —", en: "— from the table design —" },
  colorBy: { fr: "Couleur / séparer selon", en: "Colour / split by" },
  xAxisGroups: { fr: "Axe X : groupes = modalités de", en: "X axis: groups = levels of" },
  analysisN: { fr: "Analyse", en: "Analysis" },
  ci: { fr: "IC", en: "CI" },
} satisfies Record<string, Bi>;

export type Key = keyof typeof D;

interface Ctx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (k: Key) => string;
  L: (b: Bi | undefined | null) => string;
}

const I18nCtx = createContext<Ctx>(null as unknown as Ctx);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    try {
      const s = localStorage.getItem("fiole.lang");
      if (s === "fr" || s === "en") return s;
    } catch {
      /* ignore */
    }
    return navigator.language?.startsWith("fr") ? "fr" : "en";
  });
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem("fiole.lang", l);
    } catch {
      /* ignore */
    }
    document.documentElement.lang = l;
  }, []);
  const t = useCallback((k: Key) => D[k][lang], [lang]);
  const L = useCallback((b: Bi | undefined | null) => (b ? b[lang] ?? b.en : ""), [lang]);
  return <I18nCtx.Provider value={{ lang, setLang, t, L }}>{children}</I18nCtx.Provider>;
}

export const useI18n = () => useContext(I18nCtx);

/** Format a number for display in the current language (decimal comma in French). */
export function fmtNum(v: number | null | undefined, lang: Lang, digits = 4): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  let s = Math.abs(v) >= 1e5 || (Math.abs(v) < 1e-3 && v !== 0) ? v.toExponential(digits - 1) : String(Number(v.toPrecision(digits)));
  s = s.replace("-", "−");
  return lang === "fr" ? s.replace(".", ",") : s;
}

export function fmtP(display: string | undefined, lang: Lang): string {
  if (!display) return "—";
  return lang === "fr" ? display.replace(".", ",") : display;
}
