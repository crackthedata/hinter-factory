# Contracts notes (`packages/contracts/`)

The OpenAPI YAML at `packages/contracts/openapi/openapi.yaml` is the live API
contract. Its `description:` and `summary:` strings are **not stripped** by
this cleanup pass — they generate the OpenAPI document consumed by clients
and the openapi-typescript code generator. This file is the human-readable
index of the *intent* behind those strings, plus a flag for one stale line.

`packages/contracts/src/generated/api.ts` is auto-generated from the YAML by
`pnpm contracts:generate` (script defined at `packages/contracts/package.json`).
Do not edit it by hand.

---

## Stale description (please review)

- **`packages/contracts/openapi/openapi.yaml:78`** — currently reads:
  > "Deletes the project and all of its scoped data. The Default project
  > cannot be deleted."

  This is no longer true. The "Default project" guard was removed when
  mandatory `project_id` scoping landed; today every project — including one
  named "Default" — can be deleted via the API. See the regression test
  `test_any_project_can_be_deleted` in
  `services/ml/tests/test_projects.py` and the rationale documented in
  [`docs/notes-ml.md`](./notes-ml.md) → `routers/projects.py` →
  Decisions → "Defense-in-depth cascade on delete".

  Suggested rewrite:
  ```yaml
  description: Deletes the project and all of its scoped data (cascades to
    documents, tags, labeling functions, gold labels, LF runs, votes, and
    probabilistic labels).
  ```

  The `"400": Cannot delete Default` response on lines 87-88 of the same
  file is also obsolete — the route no longer returns 400 from this branch.

---

## Top-level intent

### Mandatory project scoping

- `packages/contracts/openapi/openapi.yaml:5-12` (`info.description`)
  > "Every scoped endpoint (documents, tags, labeling functions, LF runs,
  > gold labels, probabilistic labels, evaluation) requires a `project_id`
  > query parameter (or, for multipart uploads, a `project_id` form field).
  > There is no implicit fallback project; calls that omit it return 400.
  > Use `GET /v1/projects` to discover available projects and
  > `POST /v1/projects` to create one. The web UI threads `project_id`
  > through every request automatically via its project context."

  Pairs with `services/ml/app/project_scope.py` (server enforcement) and
  `apps/web/lib/api.ts` + `apps/web/lib/ml-fetch.ts` (client injection).

### "Optional in the schema, required at runtime"

- `packages/contracts/openapi/openapi.yaml:261-265` (list tags) and `:285-290`
  (create tag)
  > "Project to scope the listing to. Required at the runtime / API level
  > (the server returns 400 when it's missing); marked optional in the
  > schema only so the web client's openapi-fetch middleware can inject it
  > transparently from the active project header."

  Same pattern applies to every other scoped endpoint that takes
  `project_id` via query: the schema marks it optional so the generated
  client typings let middleware fill it in, and the runtime returns 400
  if it's still missing by the time the request reaches the server.

- `packages/contracts/openapi/openapi.yaml:285-290` (create tag, body precedence)
  > "Wins over a `project_id` field in the request body if both are
  > present."

  Cross-link: [`docs/notes-ml.md`](./notes-ml.md) → `routers/tags.py` →
  Decisions.

---

## Per-route intent

### `POST /v1/documents/upload`

- `packages/contracts/openapi/openapi.yaml:158`
  > "CSV column name for document body" (`text_column`)
- `packages/contracts/openapi/openapi.yaml:161`
  > "Optional CSV column to use as stable id" (`id_column`)

Implementation contract (not in OpenAPI strings, captured here for context):
the upload route ALSO accepts `project_id` as a multipart form field, in
addition to the query parameter — see
[`docs/notes-ml.md`](./notes-ml.md) → `routers/documents.py` → "Project id
accepted from form OR query".

### `GET /v1/documents`

- `packages/contracts/openapi/openapi.yaml:702` (`LengthBucket` description)
  > "short <100 chars, medium 100-499, long 500+"

  Server-side definitions are in `services/ml/app/routers/documents.py`
  `_length_clause`.

### `POST /v1/projects/import`

- `packages/contracts/openapi/openapi.yaml:118` (operation summary)
  > "Import a project bundle. UUIDs are re-minted; relationships are
  > preserved."
- `packages/contracts/openapi/openapi.yaml:120-123` (`target_name`)
  > "Override the bundled project name. Suffixes on collision either way."

  Cross-link: `routers/projects.py` `_unique_project_name`.

### `GET /v1/projects/{project_id}/export`

- `packages/contracts/openapi/openapi.yaml:104` (`include_runs`)
  > "Include the latest completed LF run per tag (with all votes)."

### `GET /v1/lf-runs/{run_id}/matrix`

The `SparseLabelMatrix` shape carries integer indices into parallel arrays:
- `packages/contracts/openapi/openapi.yaml:858`
  > "Index into document_ids" (`d`)
- `packages/contracts/openapi/openapi.yaml:859`
  > "Index into labeling_function_ids" (`l`)
- `LfVote` for `v`.

The `labeling_function_ids` array is laid out in the order recorded in
`LfRunLabelingFunction.position`, see [`docs/notes-ml.md`](./notes-ml.md)
→ `models.py` → Decisions → "Stable LF column ordering for matrix exports".

### `GET /v1/evaluation`

- `packages/contracts/openapi/openapi.yaml:491` (operation summary)
  > "Confusion stats and FP/FN list for a tag's gold-labeled validation set"
- `packages/contracts/openapi/openapi.yaml:499`
  > "LF run to evaluate; defaults to latest completed run for tag"
- `packages/contracts/openapi/openapi.yaml:914-920` (`EvaluationCategory`)
  > "Per-document classification of an LF-run prediction against the gold
  > label.
  > false_negative = gold +1 but prediction is -1.
  > abstain_on_positive = gold +1 but no LF fired (prediction 0); also
  > counts as a missed positive in recall.
  > false_positive = gold -1 but prediction is +1.
  > abstain_on_negative = gold -1 but no LF fired; not a false positive but
  > lowers coverage.
  > gold_abstain = gold value 0; excluded from precision/recall/F1."

  Authoritative seven-way categorization that the server enforces in
  `services/ml/app/evaluation.py:categorize`.

- Metric formulas (`EvaluationSummary`, lines 977-991):
  > `precision`: "TP / (TP + FP)"
  > `recall`: "TP / (TP + FN + abstain_on_positive)"
  > `coverage`: "Fraction of considered docs where the prediction is
  > non-abstain"

  The recall denominator deliberately includes `abstain_on_positive` — see
  `docs/notes-ml.md` → `evaluation.py` → Decisions → "Recall denominator
  includes abstain-on-positive".

- `packages/contracts/openapi/openapi.yaml:960` (`total_gold`)
  > "All gold-labeled docs for the tag (incl. gold == 0)"
- `packages/contracts/openapi/openapi.yaml:963` (`considered`)
  > "Gold-labeled docs with gold != 0; the validation set used for metrics"
- `packages/contracts/openapi/openapi.yaml:1011-1012` (`truncated`)
  > "True when more rows existed than the limit; summary counts are exact
  > regardless."
- `packages/contracts/openapi/openapi.yaml:1014-1015` (`message`)
  > "Optional human-readable explanation (e.g. when no run exists yet)"

  Mirrored in the web client; see `apps/web/app/evaluation/page.tsx`.

### `GET /v1/gold-labels`

- `packages/contracts/openapi/openapi.yaml:528` (`document_ids` query)
  > "Repeat query key for each id (e.g. document_ids=a&document_ids=b)"

  This is a recurring convention in this API: array query parameters use
  `style: form, explode: true`.

- `packages/contracts/openapi/openapi.yaml:891` (`GoldLabel.value`)
  > "Gold / manual label aligned with LF votes (1 positive, -1 negative,
  > 0 abstain)"

### Common atom: `LfVote`

- `packages/contracts/openapi/openapi.yaml:810`
  > "1 positive for tag, -1 negative, 0 abstain"

  Used everywhere a per-document vote is exchanged: gold labels, LF preview
  output, evaluation rows, sparse matrix entries.

### Common atom: `LabelingFunction.config`

- `packages/contracts/openapi/openapi.yaml:762`
  > "Type-specific JSON (patterns, thresholds, etc.)"

  Concrete shape per `LabelingFunctionType` lives in `lf_executor.py`:
  - `regex`: `{ pattern: string, flags?: "i" | "" }`
  - `keywords`: `{ keywords: string[], mode?: "any" | "all" }`
  - `structural`: `{ length_gte?, length_lte?, caps_ratio_gte?,
    caps_ratio_lte?, punctuation_ratio_gte?, punctuation_ratio_lte? }`
  - `zeroshot`, `llm_prompt`: stubs that abstain (see
    [`docs/notes-ml.md`](./notes-ml.md) → `lf_executor.py` → Future
    enhancements).

---

## `packages/contracts/src/index.ts`

Single re-export of the openapi-typescript output (`paths`, `components`,
`operations` types from `./generated/api`). No comments to migrate.

---

## `packages/contracts/src/generated/api.ts`

Auto-generated header (kept untouched):
> "This file was auto-generated by openapi-typescript. Do not make direct
> changes to the file."

Regenerate via `pnpm contracts:generate` after edits to `openapi.yaml`.
