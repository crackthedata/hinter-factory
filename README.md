# hinter-factory

Monorepo for the Hinter Factory MVP: a Next.js UI, a FastAPI ML/API service, and shared OpenAPI contracts with TypeScript types for the web client.

## Repository layout

| Path | Role |
|------|------|
| `apps/web` | Next.js (App Router), Tailwind, Explore + LF Studio + Evaluation pages |
| `services/ml` | FastAPI, SQLAlchemy, SQLite corpus store, ingest, LF execution, matrix export, evaluation |
| `packages/contracts` | OpenAPI spec (`openapi/openapi.yaml`) and generated-style TS types (`src/generated/api.ts`) consumed by the web app |

The web app proxies API traffic through Next.js rewrites: browser calls go to `/api/ml/...`, which forwards to the ML service on port 8000.

## Prerequisites

- **Node.js** and **pnpm** (repo pins `packageManager` in `package.json`) for the web app and workspace installs.
- **Python 3.11+** for `services/ml` (3.13 is fine).

## Install

From the repository root (after **Node.js** and **pnpm** are on your PATH):

```bash
pnpm install
```

### Installing Node.js and pnpm on Windows

1. Install **Node.js** (LTS) from [https://nodejs.org](https://nodejs.org). That provides `node` and `npm`. Close and reopen PowerShell or Windows Terminal so `PATH` updates.
2. Install **pnpm** using either:
   - **Corepack (recommended, matches `packageManager` in `package.json`):** `corepack enable`, then `corepack prepare pnpm@9.15.4 --activate`.
   - **npm:** `npm install -g pnpm`.
3. In PowerShell or Windows Terminal, `cd` to the folder that contains `pnpm-workspace.yaml`, then run `pnpm install` as above.

If PowerShell later blocks `Activate.ps1` for the Python venv, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use **Command Prompt** with `services\ml\.venv\Scripts\activate.bat` instead.

Python service (recommended: virtual environment in `services/ml`):

```bash
cd services/ml
python -m venv .venv
```

Activate the venv (Windows PowerShell):

```bash
.\.venv\Scripts\Activate.ps1
```

Then install the package in editable mode with dev dependencies:

```bash
pip install -e ".[dev]"
```

## Local development

Start the ML API and the web app. From the **repository root**, both can run in parallel:

```bash
pnpm dev
```

This runs `pnpm --filter @hinter/web dev` and `pnpm --filter @hinter/ml dev` together. Defaults:

- **Web:** http://localhost:3000 (redirects to `/explore`)
- **API:** http://localhost:8000 (health: `GET http://localhost:8000/healthz`)

Run them separately if you prefer:

```bash
pnpm --filter @hinter/ml dev
pnpm --filter @hinter/web dev
```

The Next.js dev server rewrites `/api/ml/:path*` to `http://127.0.0.1:8000/:path*`, so the UI talks to the API through the same origin. Ensure nothing else is bound to port 8000.

### Environment

The API reads optional settings with the `HINTER_` prefix (see `services/ml/app/config.py`). By default it uses SQLite at `services/ml/data/hinter.db` (the parent directory is created on startup). CORS allows `http://localhost:3000` by default; adjust `HINTER_CORS_ORIGINS` if your web origin differs.

## Build

From the repository root:

```bash
pnpm build
```

This builds the Next.js app (`@hinter/web`). The Python service is not bundled by this command.

## OpenAPI and TypeScript types

The canonical API description lives at `packages/contracts/openapi/openapi.yaml`. After you change it, regenerate the checked-in TypeScript module (requires Node):

```bash
pnpm contracts:generate
```

That runs `openapi-typescript` in `@hinter/contracts` and overwrites `packages/contracts/src/generated/api.ts`. The web client imports types from `@hinter/contracts` and uses `openapi-fetch` with those paths.

## Features in this repo

- **Explore:** CSV/JSON ingest, full-text search, length buckets (short / medium / long), facets on top-level JSON metadata keys, manual gold labeling, and an expandable text view for long documents.
  - **Ingest UX.** Pick a file, then click **Upload** — uploading is a separate step from picking a file, so you can edit the **Text column** / **Id column** before submitting and retry without re-picking the file. CSV: set **Text column** to the header that holds each document's body (case-insensitive match; default `text`); optionally set **Id column** for stable row ids; all other headers become JSON metadata.
  - **Reading long documents.** Each result row truncates to ~220 characters. Click **Show full** on a row to expand it (displayed with preserved whitespace), or **Expand all** above the table to expand every row on the page.
  - **Manual gold labels.** After you create tags in LF Studio, pick a tag at the bottom of the Explore filters and vote **+1** (positive for tag), **0** (abstain / unsure), or **−1** (negative) per document. Labels apply to the documents shown on the current page (default limit 50). The same `+1 / 0 / −1` semantics apply to LF votes.
- **LF Studio:** Author regex, keyword, and structural labeling functions; preview votes on sample rows; run a batch over the corpus; export a sparse label matrix from a completed run.
- **Evaluation:** For a chosen tag, treats every gold-labeled document as a validation example, aggregates LF votes from a run, and surfaces the documents the system would currently get **wrong** so you can fix the responsible LFs.
  - **Validation set.** All documents with a gold label whose value is `+1` or `−1` for the selected tag. Gold value `0` is reported as `gold_abstain` and excluded from precision / recall / F1.
  - **Predicted label.** Sum-of-votes majority over the run's LF votes per document: sum > 0 → predict `+1`, sum < 0 → predict `−1`, sum == 0 → abstain. Pure function in `services/ml/app/evaluation.py:aggregate_vote` if you want to swap in a label model later.
  - **Default run.** The latest *completed* `LfRun` for the tag (override with the **LF run** dropdown).
  - **Confusion buckets** (per gold-labeled doc):
    - `true_positive` — gold +1, predicted +1.
    - `false_negative` — gold +1, predicted −1.
    - `abstain_on_positive` — gold +1, predicted 0 (no LF fired). Counted as a miss in **recall**, since in production this document would have been overlooked.
    - `false_positive` — gold −1, predicted +1.
    - `true_negative` — gold −1, predicted −1.
    - `abstain_on_negative` — gold −1, predicted 0. **Not** a false positive, but lowers `coverage`.
    - `gold_abstain` — gold value 0; excluded from metrics.
  - **Metrics.** `precision = TP / (TP + FP)`, `recall = TP / (TP + false_negative + abstain_on_positive)`, `coverage = (TP + TN + FP + FN) / considered`, `f1 = 2·P·R / (P + R)`. Each is `null` when its denominator is 0.
  - **Per-document drill-down.** Each error row shows the document text, gold/predicted badges, the vote sum, and a **Per-LF votes** dropdown listing every LF in the run and how it voted on that document — so you can see which LF (or absence of LF) caused the mistake and edit it in Studio.

## Typical workflow

1. **Ingest a CSV/JSON corpus** in *Explore* (set the right text column, click Upload).
2. **Create a tag** in *LF Studio* (e.g. "is_invoice").
3. **Author one or more labeling functions** for that tag (regex, keywords, structural). Use **Preview** on a few sample docs.
4. **Gold-label a validation set** in *Explore*: pick the tag, then vote `+1` / `−1` on a couple dozen documents you're confident about. Use `0` only when you genuinely can't tell.
5. **Run the LFs** on the full corpus from *LF Studio*.
6. **Open *Evaluation***, pick the tag. Read the false-negative and false-positive lists, expand the per-LF breakdowns, and use what you learn to tighten or add LFs in Studio. Re-run and re-evaluate.

## API surface

Beyond the documents / tags / LF / LF-run / gold-label / probabilistic-label endpoints, two additions support the evaluation flow:

- `GET /v1/lf-runs?tag_id=&status=&limit=` — list LF runs (used by the Evaluation page's run picker).
- `GET /v1/evaluation?tag_id=…[&run_id=…]` — confusion-matrix summary plus per-document FP/FN rows for the validation set. Returns a friendly empty payload (with a `message`) if there is no completed run for the tag yet. See `EvaluationResponse` in `packages/contracts/openapi/openapi.yaml`.

**Gold labels (API):** `POST /v1/gold-labels` accepts `value` **−1**, **0**, or **1**. `GET /v1/gold-labels` supports `tag_id`, optional `document_id`, and repeated **`document_ids`** query parameters to fetch many rows at once.

## Python tests

From `services/ml` with the venv activated:

```bash
python -m pytest
```

Tests live under `services/ml/tests/` (`test_smoke.py`, `test_ingest_csv.py`, `test_gold_labels.py`, `test_evaluation.py`).
