# Fiole — architecture (MVP, phase 1)

Fiole is a web app for biologists to import data, run statistics and produce
publication-quality figures. Phase 1 is single-user (no sharing). Phase 2 adds
sharing in the Google Docs model. The data model and permission layer are built
for phase 2 now so that sharing is an extension, not a rewrite.

## Stack

| Layer     | Choice |
|-----------|--------|
| Frontend  | React 18 + TypeScript + Vite. No UI framework; small hand-written component set. FR/EN i18n via a dictionary. |
| Backend   | Python 3.12+, FastAPI, SQLAlchemy 2, SciPy, statsmodels, pandas, openpyxl, CairoSVG |
| Database  | PostgreSQL (JSONB for table contents and configurations) |

## Workspace model: Data → Analyses → Figures

```
Project (stable URL /p/<uuid>, owner, members[])
 ├─ Dataset  (version n)  ──snapshots──>  DatasetVersion 1..n  (immutable)
 │                         └─ RawUpload (original file bytes, never modified)
 ├─ Analysis (config, version) ──> AnalysisResult (dataset_version, result JSON)
 └─ Figure   (config, version) ──> renders from Dataset + optional AnalysisResult
```

* Every save of a dataset creates an immutable `DatasetVersion`. The original
  uploaded file is kept as a `RawUpload`. Exclusions are stored as a list of
  `{row_id, reason}` on the dataset and are reversible; excluded rows stay in
  the table.
* An `AnalysisResult` records the dataset version it was computed from. When the
  dataset's current version differs, the analysis (and any figure showing its
  p-values) is shown as **stale / à recalculer**.
* Missing values are stored as `null`. Nothing converts them to zero; each
  analysis reports how many values were missing and dropped.

## Phase-2 readiness (sharing)

* `project_members(project_id, user_id, role)` already exists. Roles enum:
  `owner | editor | commenter | viewer`. Phase 1 only creates `owner` rows.
* **One authorization layer**: `app/authz.py` exposes
  `require(db, user, project_id, Action.X)`. Every project-scoped endpoint goes
  through it. Actions (`VIEW`, `EDIT`, `COMMENT`, `MANAGE_SHARING`, `DELETE`,
  `EXPORT`, …) map to roles in one table. Sharing only needs new endpoints
  to manage `project_members` plus a `general_access` column; the checks are
  already in place.
* Projects have a UUID and a stable URL that does not depend on the owner.
* Datasets, analyses and figures carry an integer `version`. Updates must send
  the version they are based on (`If-Match` semantics in the body); a mismatch
  returns **409 Conflict** instead of silently overwriting. This is the basis
  for concurrent editing later.
* "Shared with me" in the dashboard is already a real query on
  `project_members` (empty in phase 1).
* An `audit_events` table records who changed what (needed for history and
  collaboration later).

## Statistics engine

`app/stats/` uses a registry: each analysis type declares its id, the design
questions it needs (independent/paired, experimental unit, technical
replicates), its inputs, and a `run()` function returning a structured result:

```
{ test, n, statistic, df, p, effect_size {name, value, ci}, assumptions[],
  diagnostics{}, warnings[], method_text{en, fr}, table[] }
```

Rules enforced by the engine:

* No automatic test choice from a normality test. Normality tests are shown as
  diagnostics only.
* p-values are never displayed as 0: below 2.2e-16 they are shown as `< 2.2e-16`.
* Technical replicates can be averaged within the experimental unit before the
  test, and n is reported as the number of experimental units.
* Problems are reported as explicit warnings or errors (n too small, zero
  variance, unmatched pairs, etc.), never hidden.
* Values are validated against R 4.x reference outputs (`backend/tests/reference`).

Phase 1 implements: descriptive statistics with CIs, Student/Welch/paired
t-tests, Mann-Whitney, Wilcoxon signed-rank, Pearson and Spearman correlation,
simple linear regression. ANOVA, Kruskal-Wallis, dose-response, two-way ANOVA,
repeated measures, mixed models and survival are listed in the UI as
"coming soon" and plug into the same registry.

## Figures and export

Figures are rendered **once, on the server, as SVG** (`app/figures/`). The
preview in the browser is that exact SVG. PDF and PNG are derived from the same
SVG with CairoSVG, so exports match the preview. Dimensions are set in mm/cm
and written as physical units in the SVG (`width="85mm"`). PNG resolution is
set in DPI. Text stays text in SVG and PDF.

Phase 1 figure types: grouped scatter (individual points), box plot, violin,
bar + points + error bars, XY scatter with regression line, line / time series.
Error bars: SD, SEM or 95% CI of the mean; centre: mean or median, with the
definition written in the figure legend text. Default palettes are
colour-blind safe (Okabe–Ito, Tol). p-value brackets can only be drawn from a
saved analysis result; manual annotations are labelled as manual.

## Not in phase 1 (shown as unavailable in the UI)

Sharing, roles beyond owner, access requests, comments, project duplication,
multi-panel figure composer, data transformations, dose-response figures and
fit, ANOVA family, full report export. (Data CSV, results CSV and the project
archive with re-import are implemented.)
