# Fiole

Statistics and publication-quality figures for biology researchers, inspired by
the way people use GraphPad Prism, with its own look. French and English UI.

**Status: MVP (phase 1, single user, no sharing).** See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and the phase-2
(sharing) preparation.

## What works

| Area | Phase 1 |
|---|---|
| Accounts | Email + password sign-up / sign-in, httpOnly session cookie |
| Dashboard | My projects, Shared with me (empty until phase 2), Recent |
| Projects | Stable URL `/p/<uuid>`, rename, soft delete, owner membership |
| Import | CSV, TSV, Excel `.xlsx` (sheet picker), paste from Excel; delimiter + decimal (`,`/`.`) detection with preview and override; original file kept byte-for-byte |
| Data | Editable grid, paste blocks from Excel, add/delete rows & columns, numeric/text columns, wide and long layouts with explicit roles (groups, values, subject, technical replicate, time, condition); missing values stay missing; reversible row exclusions with a reason; autosave with a version history |
| Analyses | Descriptive stats with CIs; Student, Welch and paired t-tests; Mann–Whitney; Wilcoxon signed-rank; Pearson & Spearman correlation; simple linear regression. Design questions (independent/paired, experimental unit, averaging technical replicates), assumptions, diagnostics, warnings, effect sizes with CIs, bilingual methods text |
| Staleness | Each result records the data version and analysis version it used; changed data/settings mark analyses and the figures using their p-values as "needs re-run" |
| Figures | Grouped scatter, bar + points, box, violin, XY + regression, line/time series. Titles, units, group order/labels/colours, colour-blind-safe palettes, fonts, sizes, line widths, symbols, axis limits, linear/log scales, mean/median with SD/SEM/95% CI/IQR/range, p-value brackets from saved analyses only, manual text clearly tagged, size in mm |
| Exports | SVG, PDF (vector, embedded font) and PNG (chosen dpi), white or transparent background, all from the same server-rendered SVG as the preview; data CSV; all results CSV; project archive (JSON) with re-import (results are recomputed on import) |

Shown in the UI as **coming soon**: sharing & roles, comments, project
duplication, one-way/two-way/repeated-measures ANOVA, Kruskal–Wallis,
dose–response (4PL), mixed models, survival, multi-panel composer, data
transformations, full report export.

## Statistical validation

`backend/tests/test_stats_reference.py` compares every implemented test with
values produced by base R 4.3 (`backend/tests/reference/make_reference.R`):
t-tests (incl. one-sided and 90% CI), Mann–Whitney and Wilcoxon (exact and
normal approximation with ties), Cohen's d / d_z CIs (noncentral t), Pearson
(with Fisher-z CI), Spearman, linear regression (coefficients, SEs, CIs, R², F),
Shapiro–Wilk and quartiles (type 7). Agreement is within 1e-9 relative for
most values. Edge cases (n too small, zero variance, unmatched pairs,
exclusions, technical replicates, p below 2.2e-16 never shown as 0) are in
`test_stats_edge_cases.py`.

## Put it online (Render, one click)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/GabKrouk/Fiole)

The button reads `render.yaml`: it creates a web service (built from the
`Dockerfile`) and a Postgres database, and connects them. Render's free tier
sleeps after 15 minutes of inactivity (the first visit then takes ~1 minute) and
the free database expires after 30 days; switch both to a paid plan
(about $7/month each) to keep data permanently.

## Run locally

Requirements: Python 3.11+, Node 18+, PostgreSQL 14+.

```bash
# database
createuser app -P          # password: app (or set FIOLE_DATABASE_URL)
createdb -O app statsapp

# backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8000

# frontend (dev, with hot reload; proxies /api to :8000)
cd frontend
npm install
npm run dev                # http://localhost:5173

# or production build served by the backend at http://localhost:8000
npm run build
```

Configuration (environment variables): `FIOLE_DATABASE_URL`
(default `postgresql+psycopg2://app:app@localhost:5432/statsapp`),
`FIOLE_COOKIE_SECURE=1` behind HTTPS, `FIOLE_FRONTEND_DIST`.

### Sharing, emails, admin (optional settings)

| variable | what it does |
|---|---|
| `FIOLE_ADMIN_EMAILS` | comma-separated accounts that see the admin page (`/admin`): counts and the CSV of people who opted in to the yearly donation appeal |
| `FIOLE_USAGE_KEY` | the `ADMIN_KEY` of the [tools-usage](https://github.com/GabKrouk/tools-usage) dashboard: each sign-in reports the account email there (server to server). Unset = nothing sent |
| `FIOLE_USAGE_ENDPOINT` | the tools-usage Apps Script URL (defaults to the one in `tools-usage/usage.js`) |
| `FIOLE_MAIL_FROM` + one of `FIOLE_BREVO_API_KEY`, `FIOLE_RESEND_API_KEY`, `FIOLE_SMTP_HOST` (`_PORT`, `_USER`, `_PASSWORD`) | sharing notifications by email. Unset = no email is sent, and the app says so |
| `FIOLE_PUBLIC_URL` | address used in email links (default: the request's address) |

## Tests

```bash
createdb -O app statsapp_test
cd backend
FIOLE_DATABASE_URL=postgresql+psycopg2://app:app@localhost:5432/statsapp_test pytest -q
# regenerate R references (needs R):
cd tests/reference && Rscript make_reference.R > r_reference.json
```

Schema is created automatically at startup (`create_all`). Add Alembic
migrations before the first production deployment.
