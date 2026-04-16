# hinter-factory

Monorepo for the Hinter Factory MVP: a Next.js UI, a FastAPI ML/API service, and shared OpenAPI contracts with TypeScript types for the web client.

## Repository layout

| Path | Role |
|------|------|
| `apps/web` | Next.js (App Router), Tailwind, Explore + LF Studio pages |
| `services/ml` | FastAPI, SQLAlchemy, SQLite corpus store, ingest, LF execution, matrix export |
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

- **Explore:** CSV/JSON ingest (CSV: choose which header is the document body via **Text column**, default `text`; optional **Id column** for stable row ids; other headers become metadata), full-text search, length buckets (short / medium / long), and facets on top-level JSON metadata keys. After you create **tags** in LF Studio, you can set **manual gold labels** on the visible result page: pick a tag, then for each document choose **+1** (positive for that tag), **0** (abstain), or **−1** (negative), matching the same vote semantics as labeling functions. Labels apply to the documents shown on the current page (default limit 50).
- **LF Studio:** Author regex, keyword, and structural labeling functions; preview votes on sample rows; run a batch over the corpus; export a sparse label matrix from a completed run.

**Gold labels (API):** `POST /v1/gold-labels` accepts `value` **−1**, **0**, or **1**. `GET /v1/gold-labels` supports `tag_id`, optional `document_id`, and repeated **`document_ids`** query parameters to fetch many rows at once.

## Python tests

From `services/ml` with the venv activated:

```bash
python -m pytest
```

Smoke tests live under `services/ml/tests/`.
