# ML service notes (`services/ml/`)

Migrated comments worth preserving from the FastAPI/SQLAlchemy/Polars service.
Each entry cites the source `path:line` it was lifted from. The corresponding
inline comment in code has been replaced with a one-line breadcrumb pointing at
the section here.

Sections per file are grouped under: **Decisions**, **Assumptions**,
**Migration notes**, **Warnings**, **Future enhancements**, and **Notes**.

---

## `services/ml/app/main.py`

No narrative comments to migrate. Behavior is discoverable from the lifespan
hook and the router list: it creates SQLAlchemy tables, runs the project
migration once (see [`projects_migration.py`](#servicesmlappprojects_migrationpy)),
and registers all `v1` routers.

---

## `services/ml/app/config.py`

No narrative comments to migrate. Reads `HINTER_`-prefixed env vars (notably
`HINTER_DATABASE_URL` and `HINTER_CORS_ORIGINS`) and ensures the SQLite parent
directory exists before the engine connects.

---

## `services/ml/app/database.py`

### Notes

- SQLite engines are created with `check_same_thread=False` so that the FastAPI
  request-thread / asyncio-thread split (see [`routers/documents.py`](#servicesmlapproutersdocumentspy)
  ingest path) can share connections through the SQLAlchemy session pool.

---

## `services/ml/app/models.py`

### Decisions

- **Stable LF column ordering for matrix exports**
  (`services/ml/app/models.py:102-103`)
  > "Which labeling functions participated in a run (stable ordering for matrix
  > columns)."

  The `LfRunLabelingFunction` join table exists so `GET /v1/lf-runs/{id}/matrix`
  can reconstruct LF columns in the exact order they were registered when the
  run was created, even after LFs are added or deleted later.

---

## `services/ml/app/ingest.py`

### Assumptions

- **Windows / Excel CSV encoding zoo** (`services/ml/app/ingest.py:22-23`)
  > "Decode CSV bytes; Windows/Excel often emits UTF-16-LE or legacy Windows
  > encodings."

  We detect a UTF-16 BOM first, then try `utf-8-sig`, then `cp1252`, then
  `latin-1`. This is the practical superset of what Excel-on-Windows produces
  when "Save as CSV" is clicked.

- **EU-locale semicolon delimiters** (`services/ml/app/ingest.py:43-44`)
  > "Pick a delimiter when Excel uses semicolons (common in EU locales)."

  `_sniff_delimiter` chooses tab, semicolon, or comma based on which appears
  most frequently on the first non-empty line. Comma wins on a tie.

- **Header matching is BOM-tolerant and case-insensitive**
  (`services/ml/app/ingest.py:57-58`)
  > "Match requested column name case-insensitively and ignoring BOM/whitespace
  > on headers."

  Applies to both `text_column` and `id_column` matching. UI sends a literal
  header name; we strip `\ufeff` and surrounding whitespace before comparing.

- **Polars encoding string compatibility** (`services/ml/app/ingest.py:184`)
  > "Polars accepts \"utf8\" and \"utf8-lossy\". UTF-8-with-BOM works under
  > \"utf8\"."

  When the BOM-prefixed UTF-8 path is detected we still pass `"utf8"`; Polars
  handles the BOM internally.

### Decisions

- **UTF-16 transcoding strategy** (`services/ml/app/ingest.py:139-152`,
  `:156-157`, `:172-173`)
  > "Polars has no native UTF-16 reader; transcode to a UTF-8 temp file."
  > "Streaming transcode keeps memory bounded even for very large inputs."
  > "codecs.iterdecode wraps the byte stream; we re-encode chunk-by-chunk."
  > "Re-sniff delimiter from the transcoded file."

  When the source CSV starts with a UTF-16 BOM we stream-decode it to a
  UTF-8 temp file using `codecs.getincrementaldecoder("utf-16")` over 1 MiB
  read chunks. The caller (`iter_csv_batches`) owns the transcoded path and
  is responsible for unlinking it in its `finally`. The delimiter is re-sniffed
  from the transcoded sample because the original sample was UTF-16 bytes.

- **`infer_schema=False` keeps everything as strings**
  (`services/ml/app/ingest.py:222-227`)
  > "`infer_schema=False` forces every column to Utf8, matching the old
  > `csv.DictReader` behavior where everything ends up as strings in
  > `metadata`."

  Down-stream JSON serialization in `_write_batch` (`routers/documents.py`)
  preserves arbitrary metadata as strings; type-inference would silently
  convert numeric-looking metadata columns and break round-tripping.

- **Per-batch error cap** (`services/ml/app/ingest.py:209-217`)
  > "Yield (items, errors, dropped_errors) per batch. Streams via Polars;
  > never loads the full file. items have the same shape as `parse_csv_bytes`:
  > {id, text, metadata}. The returned `errors` list is capped per batch at
  > `PER_BATCH_ERROR_CAP` entries so a malformed multi-GB file cannot grow an
  > unbounded list in memory; any additional errors in the same batch are
  > reported via `dropped_errors` so the caller can keep an accurate total."

  Cap is 100 errors per batch (`PER_BATCH_ERROR_CAP`).

- **Row counter starts at 2** (`services/ml/app/ingest.py:305`)
  > "Row counter starts at 2 because row 1 is the header (matches existing UX)."

  Error messages such as `row 7: empty 'text'` reference the source CSV row as
  it appears to a user opening the file in Excel.

### Migration notes

- **Polars 1.40+ API change** (`services/ml/app/ingest.py:222-227`)
  > "We use `scan_csv().collect_batches()` because the older `read_csv_batched`
  > API is deprecated in Polars 1.40+."

  If pinning Polars below 1.40 again, the old call style returns; today's
  pin is in `pyproject.toml`.

### Notes

- **Broad `except` is intentional** (`services/ml/app/ingest.py:239`)
  The `except Exception` on `lf.collect_schema()` is annotated `# noqa: BLE001`
  so the linter accepts a broad catch; the comment explains *why*: any Polars
  parse failure should surface as a clean `IngestError`, not bubble out as
  whatever exotic exception Polars/pyarrow raises.

---

## `services/ml/app/evaluation.py`

### Decisions

- **Validation set definition + sum-majority aggregation**
  (`services/ml/app/evaluation.py:1-12`)
  > "The 'validation set' is implicitly defined as every document the user has
  > gold-labeled for the chosen tag, with gold value != 0. Gold value 0 means
  > the labeler explicitly abstained, so it is excluded from precision/recall
  > but still reported in the totals."
  >
  > "Aggregation: per (document, tag) we sum the LF votes from the run.
  > Sum > 0 predicts +1 (positive for tag), sum < 0 predicts -1 (negative),
  > sum == 0 abstains. This matches the matrix already exposed by
  > `/v1/lf-runs/{id}/matrix` without introducing a probabilistic label model."

- **Recall denominator includes abstain-on-positive**
  (`services/ml/app/evaluation.py:215`)
  > "fn_total = fn + abstain_pos  # missed positives, including abstains"

  Production-relevant recall: a document the LFs all abstained on is, in
  production, a missed positive. So `recall = TP / (TP + FN + abstain_on_positive)`.

- **Error-first row ordering** (`services/ml/app/evaluation.py:240-249`)
  > "Order: errors first (FP, FN, abstain_on_positive), then the rest."
  > Priority is `false_positive < false_negative < abstain_on_positive <
  > abstain_on_negative < true_positive < true_negative < gold_abstain`,
  > with ties broken by `-abs(vote_sum)` (most-confident first) then doc id.

  Lets the Evaluation UI surface the most actionable rows at the top without
  any client-side sort.

### Future enhancements

- **Pluggable label model** (`services/ml/app/evaluation.py:1-12`)
  `aggregate_vote` is a pure function; swapping in a Snorkel-style label model
  later means replacing this single call site, nothing else changes.

### Notes

- **`considered` excludes `gold_abstain`** (`services/ml/app/evaluation.py:94`)
  > "considered: int  # gold != 0"

  Used as the denominator for `coverage`.

- **`coverage` interpretation** (`services/ml/app/evaluation.py:105`)
  > "coverage: float | None  # fraction of considered docs where prediction != 0"

- **Orphaned gold labels are silently ignored** (`services/ml/app/evaluation.py:168-169`)
  > "continue  # gold for a deleted doc; ignore"

  The `Document` is gone but the `GoldLabel` row survived (cascade off, or a
  race). We skip it in the eval and do not surface it.

- **Deleted-LF labels** (`services/ml/app/evaluation.py:183-187`)
  When a vote references an LF row that no longer exists in the project, the
  per-document drill-down shows it as `"(deleted LF)"` rather than 404'ing.

---

## `services/ml/app/lf_executor.py`

### Future enhancements

- **`zeroshot` and `llm_prompt` are stubs**
  (`services/ml/app/lf_executor.py:99-100`)
  Both LF types are scaffolded in the schema, OpenAPI spec, and Studio UI but
  the executor returns `0` (abstain) for them. Wire-up to a real classifier
  / LLM is intentionally deferred until the eval feedback loop is exercised
  on rule-based LFs.

---

## `services/ml/app/projects_migration.py`

### Decisions

- **No auto-created "Default" project** (`services/ml/app/projects_migration.py:1-17`)
  > "There is no auto-created 'Default' project — `project_id` is mandatory on
  > every scoped route."

  Combined with [`project_scope.py`](#servicesmlappproject_scopepy)'s 400 on
  missing `project_id`, this forces clients to be explicit about which
  workspace they're operating on.

- **Plain `TEXT` `project_id` columns, app-level FK**
  (`services/ml/app/projects_migration.py:58-59`)
  > "SQLite does not support adding a column with a FK reference inline
  > reliably across all versions; add as a plain TEXT and rely on the
  > application FK."

  Newly created tables (via `metadata.create_all`) get the proper SQLAlchemy
  `ForeignKey`; the migration only ever runs on pre-existing tables that lack
  the column.

### Migration notes

- **What this migration does** (`services/ml/app/projects_migration.py:1-17`)
  > "Adds a `project_id` column to documents, tags, labeling_functions,
  > lf_runs, gold_labels, and probabilistic_labels. For SQLite, the previous
  > global UNIQUE on tags.name needs to become UNIQUE(project_id, name);
  > since SQLite cannot drop an inline constraint, we recreate the tags
  > table when the old shape is detected."
  >
  > "Safe to call on every startup: each step is conditional on the schema's
  > current shape, so a freshly created DB is largely a no-op."

- **`tags` UNIQUE rebuild detection** (`services/ml/app/projects_migration.py:63-64`,
  `:81-86`, `:94-99`)
  > "If `tags` still has the old global UNIQUE(name), recreate it with
  > UNIQUE(project_id, name)."
  > "Fall back: query the unique indexes/constraints to confirm we need to
  > rebuild." (`PRAGMA index_list(tags)` rows are `seq, name, unique, origin,
  > partial`; we rebuild if any unique index covers exactly `["name"]`.)
  > "New schema already in place via metadata.create_all on a fresh DB."
  > "SQLite recreate-table workaround." (the standard CREATE-new / INSERT
  > SELECT / DROP / RENAME dance, with `PRAGMA foreign_keys=OFF` for the
  > duration.)

- **Order of operations on a fresh DB**
  (`services/ml/app/projects_migration.py:42-43`)
  > "Fresh DB pre-create_all; nothing to alter. The metadata.create_all call
  > in main.py will lay the schema down with the new shape."

  When the `projects` table is missing entirely, the migration is a no-op and
  the subsequent `Base.metadata.create_all` creates everything correctly.

### Warnings

- **Orphan rows are loud-warned, not auto-deleted**
  (`services/ml/app/projects_migration.py:131-132`, `:142-146`)
  > "Loud warning if rows lack project_id. They will be invisible to the API."
  > "Found %d rows in %s with NULL project_id. They will not be returned by
  > any project-scoped endpoint. Either DELETE them or assign them to a
  > project with: UPDATE %s SET project_id = '<id>' WHERE project_id IS
  > NULL;"

  Recovery is the operator's call; this is data they put there before the
  projects feature shipped.

---

## `services/ml/app/project_scope.py`

### Decisions

- **`project_id` is mandatory; no fallback**
  (`services/ml/app/project_scope.py:1-12`)
  > "All scoped routes accept `project_id` as a query (or form) parameter. It
  > is **required** — there is no implicit fallback project anymore. If
  > `project_id` is missing, callers get HTTP 400 with a clear message; if it
  > doesn't match an existing project they get 404."

  The 400 error body deliberately tells the operator how to recover:
  `"Use GET /v1/projects to list available projects or POST /v1/projects to
  create one."`

### Notes

- **Cross-link to web client injection** (`services/ml/app/project_scope.py:1-12`)
  > "The web client injects `project_id` automatically via
  > `apps/web/lib/api.ts:projectScopeMiddleware` and `apps/web/lib/ml-fetch.ts`,
  > sourced from the active project header. CLI/curl callers must pass it
  > explicitly."

  See also [`docs/notes-web.md`](./notes-web.md) for the client side.

---

## `services/ml/app/routers/documents.py`

### Decisions

- **`MAX_RETURNED_ERRORS` cap on per-row warnings**
  (`services/ml/app/routers/documents.py:27-29`)
  > "Cap how many per-row warnings we return to the client. A malformed
  > multi-GB CSV can otherwise produce a multi-megabyte JSON response that
  > no UI can show."

  Cap is `100`. The dropped count is reported as `truncated_errors_count`.

- **`INGEST_BATCH_SIZE` = 10 000** (`services/ml/app/routers/documents.py:31-34`)
  > "How many CSV rows we batch into a single executemany call. Big enough
  > to amortize SQLite overhead, small enough that one batch fits comfortably
  > in RAM even for very wide rows."

- **`MAX_UPLOAD_PART_SIZE` lifts Starlette's 1 MiB cap**
  (`services/ml/app/routers/documents.py:40-45`,
  `:288-292`)
  > "Starlette's MultiPartParser defaults max_part_size to 1 MiB and rejects
  > any file part larger than that with MultiPartException, which uvicorn
  > surfaces as a dropped connection (ECONNRESET on the proxy side). For
  > our streaming upload we want effectively no limit; file parts spool to
  > disk via SpooledTemporaryFile regardless of this number, so memory stays
  > bounded."
  >
  > "We bypass FastAPI's `File(...)` / `Form(...)` parameters because they
  > call `request.form()` with the default `max_part_size=1 MiB`. Any file
  > upload larger than 1 MiB would otherwise raise MultiPartException and
  > uvicorn would drop the socket (the client sees ECONNRESET). Calling
  > `request.form()` ourselves lets us raise the per-part ceiling."

  Cap is 64 GiB; that's a ceiling, not a target. Spool-to-disk keeps memory
  bounded regardless.

- **Bulk-write SQLite pragmas**
  (`services/ml/app/routers/documents.py:99-106`)
  > "Tune SQLite for a long bulk write. WAL is persistent on the DB file;
  > the others are per-connection and may leak back to the pool, which is
  > acceptable for this dev tool (slightly faster, slightly less durable)."

  Settings: `journal_mode=WAL`, `synchronous=NORMAL`, `temp_store=MEMORY`,
  `cache_size=-200000` (negative = KiB; ≈ 200 MiB page cache). Consciously
  trades a small amount of durability for ingest throughput in this dev
  workbench.

- **`_write_batch` upsert semantics**
  (`services/ml/app/routers/documents.py:127-136`)
  > "Upsert one batch. Returns (inserted_count, updated_count). Preserves the
  > existing semantics:
  >  - same id, same project   -> UPDATE in place
  >  - same id, other project  -> mint a fresh UUID and INSERT
  >  - new id                  -> INSERT with the supplied id"

- **Cross-project id collision policy**
  (`services/ml/app/routers/documents.py:155-158`)
  > "cross-project id collision: re-mint rather than clobber the other
  > project's row."

  Project isolation is enforced at write time, not just at read time. See the
  regression test `test_upload_endpoint_cross_project_collision_mints_new_id`
  in `services/ml/tests/test_ingest_stream.py`.

- **Why ingest runs on a worker thread**
  (`services/ml/app/routers/documents.py:201-202`,
  `:329-331`)
  > "Run the streaming ingest synchronously. This is the CPU/IO-heavy path
  > that the async route offloads to a worker thread."
  > "The ingest is sync (Polars + SQLite executemany). Run it in a worker
  > thread so the event loop stays responsive for other requests during the
  > multi-minute write of a multi-GB file."

  Implemented via `asyncio.to_thread(_ingest_sync, ...)`.

- **Long write commits via the raw DBAPI cursor**
  (`services/ml/app/routers/documents.py:203-206`,
  `:209-210`)
  > "Release any read-lock the SA session may be holding before we start a
  > long-running write transaction on the underlying connection. WAL mode
  > (set below) means readers won't block this writer either way, but this
  > keeps the session's view consistent."
  > "DBAPI connection (sqlite3.Connection wrapper)"

  `db.commit()` first, then write through the underlying `sqlite3.Connection`
  for `executemany` performance, committing per batch.

- **Project id accepted from form OR query**
  (`services/ml/app/routers/documents.py:310-311`)
  > "Accept project_id from either the form body or the query string; the
  > web client now passes it both ways."

  Form value wins, query is fallback.

### Assumptions

- **Starlette's `UploadFile` vs FastAPI's** (`services/ml/app/routers/documents.py:16-19`)
  > "Important: use Starlette's UploadFile (not fastapi.UploadFile) for the
  > isinstance check. fastapi.UploadFile is a subclass; instances returned by
  > `request.form()` are the Starlette base class, so checking against the
  > FastAPI subclass would always fail."

- **Excel-on-Windows content types** (`services/ml/app/routers/documents.py:50-51`,
  `:60-61`)
  > "Detect CSV uploads; Windows/Excel often omits 'csv' from Content-Type."
  > "Excel on Windows frequently labels comma-separated exports as:
  > `application/vnd.ms-excel` / `application/vnd.ms-excel.sheet.macroenabled.12`."

- **SQLite parameter limit chunking** (`services/ml/app/routers/documents.py:36-38`,
  `:109-113`)
  > "When we look up which incoming IDs already exist, we chunk the IN(...)
  > query below SQLite's default 999-parameter limit."
  > "Return {document_id: project_id} for the supplied ids, chunked to stay
  > under SQLite's parameter limit."

  Chunk size is `ID_LOOKUP_CHUNK = 500`.

- **Spooling rewinds the upload**
  (`services/ml/app/routers/documents.py:175-178`,
  `:180-184`)
  > "Copy the upload to a real on-disk temp file in 1 MiB chunks. Never
  > materializes the full body in Python memory; relies on Starlette's
  > UploadFile already being a SpooledTemporaryFile that spills to disk."
  > "rewind in case anything has consumed the stream" /
  > "some file-likes don't support seek" (`# noqa: BLE001`).

### Future enhancements

- **JSON path is not streaming yet**
  (`services/ml/app/routers/documents.py:222-225`)
  > "JSON path stays buffered: the JSON parser needs the whole document
  > anyway, so streaming wouldn't help. For very large JSON, callers
  > should convert to CSV."

  If/when we adopt an incremental JSON parser (`ijson`) we could lift this.

### Migration notes

- **`skipped` field name kept for UI compatibility**
  (`services/ml/app/routers/documents.py:273-276`)
  > "Field name kept as `skipped` for backward compatibility with the
  > existing UI; semantically it's 'rows that updated an existing doc'."

  Renaming is a coordinated UI/API change; don't do it casually.

### Notes

- **Error truncation contract** (`services/ml/app/routers/documents.py:90-91`)
  > "Keep only the first MAX_RETURNED_ERRORS messages; report the dropped
  > count."

---

## `services/ml/app/routers/evaluation.py`

This router only exposes `evaluate_run` over HTTP. The OpenAPI `description=`
strings (kept inline because they generate the contract) document:

- `tag_id` — defines the validation set for the run.
- `run_id` — defaults to the latest *completed* run for the tag.
- `limit` — caps returned per-document rows; summary counts are **not** capped.
- `text_preview_chars` — between 20 and 2000 (default 240).

Empty-state message when no completed run exists for the tag:
`"No completed LF run for this tag yet. Run LFs in Studio first."`

---

## `services/ml/app/routers/projects.py`

### Decisions

- **Export bundle shape and import re-mint**
  (`services/ml/app/routers/projects.py:1-8`)
  > "The export format is a single JSON document containing the project
  > metadata plus every record that belongs to it (documents, tags, labeling
  > functions, gold labels, optional latest LF run with votes, and
  > probabilistic labels). On import we always mint fresh UUIDs but preserve
  > cross-record relationships via an in-memory id map; if the project name
  > collides we suffix it."

- **Defense-in-depth cascade on delete**
  (`services/ml/app/routers/projects.py:78-79`)
  > "Cascade is configured on FKs; explicit children deletes keep things
  > safe even when SQLite PRAGMA foreign_keys is OFF in some environments."

  Order: probabilistic → gold → run-votes → run-LFs → runs → LFs → tags
  → documents → project. Run-votes and run-LFs do not carry `project_id`,
  so they're deleted via parent run id.

### Notes

- **Run-vote / run-LF deletion path**
  (`services/ml/app/routers/projects.py:90-91`)
  > "These don't have project_id; delete via parent run"

- **Skip orphaned LFs on import**
  (`services/ml/app/routers/projects.py:316-317`)
  > "continue  # orphaned LF"

  An imported LF whose `tag_id` is not in `tag_id_map` is silently dropped.

---

## `services/ml/app/routers/tags.py`

### Decisions

- **Query param wins over body for `project_id`**
  (`services/ml/app/routers/tags.py:38-41`)
  > "Project may arrive as a query param (the web client injects it that
  > way via `projectScopeMiddleware`) or, for backwards compatibility, as
  > a body field used by import flows. Query param wins so the active
  > project from the UI header is respected."

  Regression-tested by `test_create_tag_honors_project_id_query_parameter`
  in `services/ml/tests/test_projects.py`.

---

## `services/ml/app/routers/labeling_functions.py`

No narrative comments to migrate. Notable behavior (worth knowing, captured
here from code-reading): on create the LF inherits `project_id` from its
`tag_id`, so the request body does not need to repeat it; the parent tag's
project is treated as authoritative.

---

## `services/ml/app/routers/lf_runs.py`

No narrative comments to migrate. Behavior: a single POST creates the run,
walks the project's documents, executes every selected LF synchronously,
records non-zero votes, and flips the run to `completed` (or `failed` if
an `LfConfigError` is raised). The matrix endpoint reconstructs the sparse
vote matrix from `LfRunLabelingFunction.position`.

---

## `services/ml/app/routers/probabilistic.py`

No narrative comments to migrate. Thin list / upsert over the
`probabilistic_labels` table; nothing populates it today, but the table
and route are reserved for the future label-model integration described
under [`evaluation.py`](#servicesmlappevaluationpy) → Future enhancements.

---

## `services/ml/app/routers/gold_labels.py`

No narrative comments to migrate. Validates `value ∈ {-1, 0, 1}`, supports
querying by repeated `document_ids` for batch fetches, and upserts on
`(document_id, tag_id)`.

---

## `services/ml/tests/conftest.py`

### Warnings

- **Test isolation depends on env-var ordering**
  (`services/ml/tests/conftest.py:1-12`)
  > "Test isolation: redirect the API's database to a temporary file before
  > any application module is imported. Without this, tests share the dev
  > DB because `app/database.py` calls `get_settings()` (and creates the
  > SQLAlchemy engine) at import time. A `Base.metadata.drop_all()` from a
  > test would then wipe whatever the developer has in
  > `services/ml/data/hinter.db`. Setting `HINTER_DATABASE_URL` here — at
  > the very top of the conftest, before pytest collects any test modules
  > — guarantees the engine binds to a temp file instead. The temp directory
  > is removed at the end of the session."

- **Don't move the env-var assignment**
  (`services/ml/tests/conftest.py:21-23`)
  > "IMPORTANT: assign before importing anything from `app.*` (directly or
  > via test modules). Pytest loads conftest.py first, then collects tests,
  > so this runs before `from app.database import ...` ever happens."

  The `import pytest  # noqa: E402` exception is intentional and exists
  because of this constraint.

### Notes

- **Per-test schema reset uses lazy import**
  (`services/ml/tests/conftest.py:32-37`)
  > "Wipe and recreate the schema before every test for full isolation.
  > Imported lazily so that the `HINTER_DATABASE_URL` override above is in
  > effect by the time `app.database` evaluates `get_settings()`."

---

## `services/ml/tests/test_ingest_stream.py`

### Notes

- **What this file regression-guards**
  (`services/ml/tests/test_ingest_stream.py:1-13`)
  > "End-to-end tests for the streaming CSV ingest path. These exercise:
  > `iter_csv_batches` against a synthetic CSV that materializes on disk
  > only (never as a single Python bytes/str object), and the
  > `/v1/documents/upload` endpoint via FastAPI's TestClient, including
  > the cross-project ID collision behavior we promised to preserve. The
  > 'bounded memory' test generates a >100 MB file in `tmp_path` and asserts
  > that peak Python heap allocation during a full streaming pass stays
  > well below the file size. If someone accidentally re-introduces
  > `file.read()` somewhere in the path, this test will fail loudly."

- **`_write_csv` shape**
  (`services/ml/tests/test_ingest_stream.py:30-36`,
  `:42-44`)
  > "Write a CSV with `rows` data lines. Returns the file size in bytes.
  > Uses simple ASCII so we exercise the fast UTF-8 path. Each row carries
  > one `text` column plus two metadata columns so we also cover metadata
  > extraction."
  > "Buffer ~64 KiB of rows at a time to keep the test fast without
  > ballooning Python memory in the test process."

- **Bounded-memory threshold rationale**
  (`services/ml/tests/test_ingest_stream.py:115-121`)
  > "File is >100 MiB; if anything buffered the whole thing we'd be far
  > above this threshold. 80 MiB leaves headroom for batch overhead and
  > Polars internals while still catching 'buffer everything' regressions."

- **Re-upload `skipped` semantics**
  (`services/ml/tests/test_ingest_stream.py:150-151`)
  > "Re-upload the same file: every row should now hit the same-project
  > update path (skipped count == 5000, no new inserts)."

  See `routers/documents.py` → Migration notes → "skipped" field name.

- **Cross-project collision sanity checks**
  (`services/ml/tests/test_ingest_stream.py:178-198`)
  > "Same id, different project: should mint a fresh UUID rather than
  > clobber p1." / "The original p1 row is intact." / "And p2 has a row
  > with the supplied text but a fresh (UUID) id."

### Warnings

- **Starlette 1 MiB regression coverage**
  (`services/ml/tests/test_ingest_stream.py:224-228`)
  > "Regression: Starlette's MultiPartParser defaults `max_part_size` to
  > 1 MiB and aborts the connection on any larger file part. The upload
  > route must raise that ceiling itself; otherwise multi-MB CSVs surface
  > as ECONNRESET on the proxy side. We send a ~5 MiB part to prove the
  > limit was lifted."

- **Tracemalloc cleanup fixture**
  (`services/ml/tests/test_ingest_stream.py:258-260`)
  > "Keep a tracemalloc-related import side-effect from leaking if a test
  > fails mid-flight. tracemalloc.stop() in a finally above handles the
  > happy path, but pytest may abort earlier; this guards subsequent
  > tests."

---

## `services/ml/tests/test_evaluation.py`

### Notes (test-scenario annotations)

The inline `# gold +1, FP, ...` comments next to CSV row strings (lines
35-40, 70-71, 87-100, 108-110) are scenario maps that bind the literal
input to the expected confusion category. They are kept inline in the
source because they are tightly coupled to assertion semantics; the
"keyword LF only votes 0 or 1, so the 'negative' categories collapse to
abstains" pattern is an important reminder when reading the precision /
recall / F1 numerics.

This file remains the authoritative example of the seven evaluation
categories — see [`evaluation.py`](#servicesmlappevaluationpy) for the
formal definitions.

---

## `services/ml/tests/test_projects.py`

### Migration notes

- **No more "Default" project guard**
  (`services/ml/tests/test_projects.py:28-30`)
  > "With Default removed, every scoped endpoint must 400 when project_id
  > is omitted instead of silently using a fallback project."

- **Any project can be deleted**
  (`services/ml/tests/test_projects.py:96-98`)
  > "With the Default-project guard removed, every project — including
  > one that happens to be named 'Default' — can be deleted via the API."

### Decisions

- **Query-param `project_id` is what the web client uses**
  (`services/ml/tests/test_projects.py:72-76`)
  > "The web client injects project_id via the URL query string (see
  > `apps/web/lib/api.ts` `projectScopeMiddleware`), not the JSON body.
  > Make sure the POST handler reads from the query so a tag actually
  > lands in the intended project."

  Cross-link: `routers/tags.py` → Decisions.

### Notes

- **Export/import roundtrip evaluation expectations**
  (`services/ml/tests/test_projects.py:199-205`)
  > "The keyword LF voted +1 on invoice doc, 0 on pizza doc. Gold labels:
  > invoice=+1, pizza=-1. So:
  >   gold +1, pred +1 -> TP
  >   gold -1, pred  0 -> abstain_on_negative"

  This is the smallest end-to-end demonstration of the seven evaluation
  categories from a JSON bundle.

---

## `services/ml/tests/test_smoke.py`, `test_ingest_csv.py`, `test_gold_labels.py`

No narrative comments to migrate; these files are direct API exercises
whose intent is conveyed by their assertions and function names.
