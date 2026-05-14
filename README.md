# hinter-factory

Monorepo for the Hinter Factory MVP: a Next.js UI, a FastAPI ML/API service, and shared OpenAPI contracts with TypeScript types for the web client.

## Repository layout

| Path | Role |
|------|------|
| `apps/web` | Next.js (App Router), Tailwind, Explore + LF Studio + Topics + Evaluation + Projects pages, project context with header selector |
| `services/ml` | FastAPI, SQLAlchemy, SQLite corpus store, ingest, LF execution, matrix export, evaluation, topic modeling, project scoping + JSON export/import |
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

- **Projects:** A *project* is a self-contained workspace — its own documents, tags, labeling functions, gold labels, LF runs, and probabilistic labels. The active project is shown in the header picker (persisted in `localStorage`), and **every** API call from the UI is automatically scoped to it. There is **no implicit fallback project**: a fresh install has zero projects, and Explore / LF Studio / Evaluation render a "create or pick a project" empty state until you do.
  - **Switching projects.** Use the picker in the page header. Each page (Explore / Studio / Evaluation) refetches when you switch — no reload needed.
  - **Create a project.** Visit `/projects` and use *Create project*. Project names are globally unique. New projects start empty.
  - **Export / Import.** From `/projects`, click **Export** on any project to download a single `*.hinter-project.json` bundle (documents + tags + LFs + gold labels + the latest *completed* LF run per tag with all its votes + probabilistic labels). On a different Hinter Factory instance, click **Import** and choose the file: UUIDs are re-minted on the way in (so an import never collides with existing rows), relationships are preserved via an internal id map, and the project name is suffixed with " (imported)" if there's a clash.
  - **Any project can be deleted.** `DELETE /v1/projects/{id}` cascades to every row in the workspace. Use with care.
  - **Tag uniqueness is per-project.** The same tag name may exist in multiple projects.
- **Explore:** CSV/JSON ingest, full-text search, length buckets (short / medium / long), facets on top-level JSON metadata keys, manual gold labeling, and an expandable text view for long documents.
  - **Ingest UX.** Pick a file, then click **Upload** — uploading is a separate step from picking a file, so you can edit settings before submitting and retry without re-picking the file. Both CSV and JSON are accepted.
    - **CSV:** set **Text column** to the header that holds each document's body (case-insensitive match; default `text`); optionally set **Id column** for stable row ids; all other headers become JSON metadata.
    - **JSON:** upload a `.json` file containing either an array of objects or `{"documents": [...]}`. Each object must have a `"text"` key for the document body; an optional `"id"` key sets a stable ID; all other keys are stored as metadata. Example:
      ```json
      [
        { "id": "doc-1", "text": "Invoice #42 for consulting services.", "source": "email" },
        { "id": "doc-2", "text": "Your receipt is attached.", "source": "web" }
      ]
      ```
      The Ingest panel shows a format reminder and hides the CSV-only fields whenever a `.json` file is selected.
  - **Reading long documents.** Each result row truncates to ~220 characters. Click **Show full** on a row to expand it (displayed with preserved whitespace), or **Expand all** above the table to expand every row on the page.
  - **Manual gold labels.** After you create tags in LF Studio, pick a tag at the bottom of the Explore filters and vote **+1** (positive for tag), **0** (abstain / unsure), or **−1** (negative) per document. Labels apply to the documents shown on the current page (default limit 50). The same `+1 / 0 / −1` semantics apply to LF votes.
- **LF Studio:** Author regex, keyword, and structural labeling functions; preview votes on sample rows; run a batch over the corpus; export a sparse label matrix from a completed run.
  - **Suggested hinters.** After you gold-label even a few documents, the **Suggested hinters** panel mines your gold-positive and gold-negative documents for statistically predictive keywords. Each suggestion comes with a direction (`+1` or `−1`), hit counts for both gold classes, and a heuristic confidence score. Click **Add as +1 LF** / **Add as −1 LF** to create a `keywords` labeling function in one step. Before any gold labels exist, the panel falls back to tokens derived from the tag name itself ("cold-start"). The underlying miner is `suggest_keywords_for_tag` in `services/ml/app/suggestions.py`; it is invoked on demand by `GET /v1/labeling-functions/suggestions`.
- **Topic Modeling:** Unsupervised discovery of latent themes in your corpus to generate keyword hinter suggestions, with or without gold labels. See the [Topic Modeling](#topic-modeling) section for full details.
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

1. **Pick (or create) a project** from the header selector or `/projects`. Everything you do next is scoped to it.
2. **Ingest a CSV/JSON corpus** in *Explore* (set the right text column, click Upload).
3. **Create a tag** in *LF Studio* (e.g. "is_invoice").
4. **Run a topic model** in *Topics* to get a map of the corpus before you've written a single LF. Choose the number of topics (10 is a good default), pick LDA or NMF, and click **Run topic model**. While it fits in the background, continue to the next step.
5. **Manually label some documents first** in *Explore*: pick the tag in the filter bar, then vote `+1` (positive for this tag), `0` (unsure), or `−1` (negative) on a handful of documents you're confident about — even 10–20 labels is enough to seed the next steps.
6. **Review topic-based suggestions** in *Topics* once the run completes: pick the tag you created, and the panel surfaces the topics most aligned with your gold-positive documents, then ranks their top keywords as hinter candidates. Click **Add as +1 LF** on any keyword to create a `keywords` labeling function in one step. Use **Dismiss** to skip terms that look like noise.
7. **Review frequency-based suggestions** in *LF Studio* → **Suggested hinters** panel. This miner works differently — it compares raw token frequencies across gold classes — so it often surfaces complementary candidates to the topic model. Add, dismiss, and refresh as needed.
8. **Author additional labeling functions** by hand in *LF Studio* (regex, keywords, structural) for patterns neither suggestion source covered. Use **Preview** to sanity-check on sample docs before running a full batch.
9. **Run the LFs** on the full corpus from *LF Studio*.
10. **Open *Evaluation***, pick the tag. Read the false-negative and false-positive lists, expand the per-LF breakdowns, and use what you learn to tighten or add LFs in Studio. Add more gold labels in *Explore* as you discover edge cases — each new label improves both future topic-based and frequency-based suggestion quality.
11. Re-run and re-evaluate. Repeat until metrics are good enough.
12. **Share work between machines.** From `/projects`, **Export** the project to a JSON file and hand it off; on the other instance, **Import** that file to recreate the entire workspace.

## Topic Modeling

### What it is

Topic modeling is an unsupervised machine-learning technique that reads your entire corpus and groups words that tend to appear together into *topics* — clusters of related vocabulary that represent recurring themes. It does not need gold labels or labeling functions to run: it works purely from the text itself. The output is a set of topics, each described by its top-weighted words (e.g. a topic might surface `invoice`, `payment`, `billing`, `amount`, `due` — revealing an invoicing theme without you having to name it first).

Hinter Factory uses two well-established algorithms from scikit-learn:

| Algorithm | Full name | When to prefer it |
|-----------|-----------|-------------------|
| **LDA** | Latent Dirichlet Allocation | Default. Probabilistic model; topics are soft distributions over words. Good for general text with many overlapping themes. |
| **NMF** | Non-negative Matrix Factorization | Produces crisper, more separable topics. Often better when documents are short or themes are fairly distinct. |

Both operate on a bag-of-words representation of your corpus (LDA uses raw counts; NMF uses TF-IDF). English stop words are removed automatically. Rare words (appearing in fewer than 2 documents) and very common words (appearing in more than 95% of documents) are filtered out to keep the vocabulary meaningful.

### How it is implemented

**Backend** (`services/ml/app/topic_modeling.py`, `services/ml/app/routers/topics.py`):

1. A `POST /v1/topic-models` request creates a `TopicModel` row with status `pending` and immediately returns HTTP 202. A daemon thread then calls `run_topic_model()`, which:
   - Fetches all documents for the project from SQLite.
   - Builds a vocabulary capped at `max_features` words (default 5 000).
   - Fits LDA or NMF with the requested number of topics.
   - Extracts the top 20 words per topic with their weights.
   - Records each document's *dominant topic* (the topic with the highest probability for that document) as a `{doc_id: topic_index}` map.
   - Writes topics and the doc→topic map back into the `TopicModel` row and sets status `completed` (or `failed` on error).
2. `GET /v1/topic-models/{id}` returns the full model including all topics once complete.
3. `GET /v1/topic-models/{id}/suggestions?tag_id=…` computes hinter suggestions on demand:
   - Looks up gold-positive (value `+1`) and gold-negative (value `−1`) document ids for the tag.
   - Uses the stored doc→topic map to count how often each topic appears in the positive vs negative class.
   - Scores each topic by `(positive_frequency − negative_frequency)` to find the ones most discriminative for the tag.
   - Collects the top-weighted words from the highest-scoring topics, merges them weighted by topic relevance, and returns up to `limit` suggestions.
   - **Cold-start:** if there are no gold labels yet, returns top words from the largest topics so you still get candidates to work from.

**Storage:** topic model runs are scoped to a project via `project_id` (CASCADE delete). Topics and doc→topic assignments are stored as JSON columns in the `topic_models` SQLite table. Topic model data is **not** included in the project export bundle.

**Frontend** (`apps/web/app/topics/page.tsx`):

- Left sidebar: run-configuration form (topic count slider 2–50, algorithm selector, vocabulary size input) + list of past runs with live status badges that poll every 2.5 s until the run reaches a terminal state.
- Right panel: a grid of topic cards — each showing its top-10 words as opacity-weighted chips (heavier words are more opaque). Topics that are identified as relevant to the selected tag are highlighted with an accent border.
- Suggestions panel: tag selector, a "Relevant topics" legend showing which topics aligned with the tag and their relevance scores, then a ranked list of keyword candidates each with **Add as +1 LF** and **Dismiss** buttons. Dismissed words are excluded from subsequent refreshes.

### Parameters

| Parameter | Default | Notes |
|-----------|---------|-------|
| **Number of topics** | 10 | How many distinct themes to find. Use 5–10 for small or focused corpora; 15–30 for large, diverse ones. More topics = finer granularity but more noise. |
| **Algorithm** | LDA | Switch to NMF if topics feel blurry or documents are short. |
| **Max vocabulary size** | 5 000 | Caps the number of unique words considered. Lower (1 000–2 000) for small corpora; raise only if topics feel too generic on a large domain-rich dataset. |

### Using topic modeling in your workflow

**Before you have gold labels (cold-start discovery):**

Run a topic model immediately after ingesting your corpus — before creating any tags or writing any LFs. Browse the topic cards to understand what themes are actually present in your data. This often reveals categories you hadn't anticipated and helps you decide which tags are worth creating. When you open the Suggestions panel and pick a tag, the cold-start mode returns the top vocabulary from the most prominent topics, giving you a starter set of keywords even with zero gold labels.

**After labeling a few documents (gold-aligned suggestions):**

Once you have even 10–20 gold labels for a tag, the suggestion algorithm switches from cold-start to gold-aligned mode. It identifies which topics your positive examples cluster into versus your negative examples, then ranks keywords from those discriminating topics. Because the topic model considers the full distributional context of words (not just their raw frequency), it often surfaces thematically coherent keyword sets that the frequency-based miner in LF Studio misses — and vice versa. **Use both suggestion sources together** for the broadest coverage.

**Iterating:**

Topic models are cheap to re-run. If your corpus grows significantly, or if your initial run produced unsatisfying topics (too coarse, too noisy), run another with different parameters and compare. Each run is stored separately so you can keep the old one for reference. Old runs that are no longer useful can be deleted with the **Delete** button in the Past runs list.

**Typical signal-to-noise heuristics:**
- If a suggested keyword is a proper noun or domain term you recognise as highly relevant → add it.
- If a suggested keyword is generic filler (`said`, `would`, `also`) → dismiss it; it probably leaked through because the stop-word list is English-only.
- If the Relevant topics panel shows low relevance scores (close to 0) for all topics → you need more gold labels before the alignment is meaningful.

### Topic model API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/topic-models` | Start a new run. Body: `{n_topics, algorithm, max_features}`. Returns HTTP 202 with status `pending`. |
| `GET` | `/v1/topic-models` | List all runs for the active project. |
| `GET` | `/v1/topic-models/{id}` | Fetch a run. Includes `topics` array once `status` is `completed`. |
| `DELETE` | `/v1/topic-models/{id}` | Delete a run and its stored data. |
| `GET` | `/v1/topic-models/{id}/suggestions?tag_id=…[&limit=10][&exclude=word1&exclude=word2]` | Keyword suggestions for a tag. Use `exclude` to carry dismissed words across refreshes. |

## API surface

Beyond the documents / tags / LF / LF-run / gold-label / probabilistic-label endpoints, two additions support the evaluation flow plus the new project management endpoints:

- `GET /v1/lf-runs?tag_id=&status=&limit=` — list LF runs (used by the Evaluation page's run picker).
- `GET /v1/evaluation?tag_id=…[&run_id=…]` — confusion-matrix summary plus per-document FP/FN rows for the validation set. Returns a friendly empty payload (with a `message`) if there is no completed run for the tag yet. See `EvaluationResponse` in `packages/contracts/openapi/openapi.yaml`.
- `GET /v1/projects` / `POST /v1/projects` / `GET /v1/projects/{id}` / `DELETE /v1/projects/{id}` — list, create, fetch (with counts), and delete projects. Any project can be deleted; the cascade clears its documents, tags, LFs, runs, gold labels, and probabilistic labels.
- `GET /v1/projects/{id}/export?include_runs=true` — return the full project JSON bundle (see *Export format* below).
- `POST /v1/projects/import?target_name=…` — accept a previously exported bundle and create a fresh project from it.

**Project scoping (every other endpoint).** Every list/create/upload endpoint **requires** a `project_id` query (or, for multipart uploads, form) parameter. Calls without it return HTTP 400 with a descriptive error. The web UI threads the active project id through `lib/ml-fetch.ts` and `lib/api.ts` automatically; CLI / curl callers must pass it explicitly. Use `GET /v1/projects` to discover ids.

**Gold labels (API):** `POST /v1/gold-labels` accepts `value` **−1**, **0**, or **1**. `GET /v1/gold-labels` supports `tag_id`, optional `document_id`, and repeated **`document_ids`** query parameters to fetch many rows at once.

### Export format

A project bundle is a single JSON object with shape:

```json
{
  "format": "hinter-factory.project",
  "format_version": 1,
  "exported_at": "...",
  "project": { "name": "...", "description": "..." },
  "documents": [{ "id", "text", "metadata", "char_length", "created_at" }],
  "tags": [{ "id", "name", "taxonomy_version", "created_at" }],
  "labeling_functions": [{ "id", "tag_id", "name", "type", "config", "enabled", "created_at" }],
  "gold_labels": [{ "id", "document_id", "tag_id", "value", "note", "created_at" }],
  "lf_runs": [{
    "id", "tag_id", "status", "documents_scanned", "votes_written",
    "created_at", "completed_at", "labeling_function_ids": [...],
    "votes": [{ "document_id", "labeling_function_id", "vote" }]
  }],
  "probabilistic_labels": [{ "document_id", "tag_id", "probability", "conflict_score", "entropy", "updated_at" }]
}
```

Bundle ids are the source-instance ids; on import, `/v1/projects/import` re-mints all UUIDs and re-links them through an internal id map. Pass `?include_runs=false` to omit LF runs from the export (smaller file, no votes carried over). See `ProjectExport` in `packages/contracts/openapi/openapi.yaml` for the schema.

### Storage and migrations

The schema is created via `Base.metadata.create_all` on startup. An idempotent migration (`services/ml/app/projects_migration.py`) then:

1. Adds `project_id` columns to `documents`, `tags`, `labeling_functions`, `lf_runs`, `gold_labels`, and `probabilistic_labels` if missing (SQLite `ALTER TABLE`).
2. Rebuilds the `tags` table once if the old global `UNIQUE(name)` constraint is detected (since SQLite can't drop an inline unique constraint), replacing it with `UNIQUE(project_id, name)`.
3. Logs a warning at startup for any rows still carrying `NULL project_id` — those rows are invisible to every scoped endpoint until you assign them to a project (`UPDATE … SET project_id = '<id>' WHERE project_id IS NULL;`) or delete them.

The migration is safe to run on every boot. There is **no** auto-created "Default" project anymore — `project_id` is mandatory on every scoped request, and `app/project_scope.py:resolve_project_id` returns 400 when it's missing.

## Headless Batch Processing

You can run labeling functions across a new CSV file without using the web UI or importing the documents into a project. The headless script reads an input CSV, applies a project's enabled labeling functions, computes probabilities, and writes a new CSV with the probability columns appended.

From `services/ml` with the venv activated:

```bash
python headless.py \
  --project-name "Your Project Name" \
  --input-csv path/to/input.csv \
  --output-csv path/to/output.csv \
  --text-column "text_column_name"
```

The `--text-column` value must match the CSV header exactly (case-sensitive). If it doesn't match, the script prints the available column names and exits.

By default the script uses the same SQLite database as the API (`services/ml/data/hinter.db`). If your database lives elsewhere, set `HINTER_DATABASE_URL` before running:

```bash
HINTER_DATABASE_URL=sqlite:////absolute/path/to/hinter.db python headless.py ...
```

On Windows PowerShell:

```powershell
$env:HINTER_DATABASE_URL = "sqlite:///C:/path/to/hinter.db"
python headless.py ...
```

## Python tests

From `services/ml` with the venv activated:

```bash
python -m pytest
```

Tests live under `services/ml/tests/` (`test_smoke.py`, `test_ingest_csv.py`, `test_gold_labels.py`, `test_evaluation.py`, `test_projects.py`).
