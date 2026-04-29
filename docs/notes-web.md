# Web app notes (`apps/web/`)

Migrated comments worth preserving from the Next.js (App Router) frontend.
Each entry cites the source `path:line` it was lifted from. The corresponding
inline comment in code has been replaced with a one-line breadcrumb pointing
at the section here.

Sections per file are grouped under: **Decisions**, **Assumptions**,
**Future enhancements**, **Warnings**, and **Notes**.

---

## `apps/web/next.config.ts`

### Decisions

- **`middlewareClientMaxBodySize: "4gb"` for proxied uploads**
  (`apps/web/next.config.ts:6-25`)
  > "Next.js 15.5+ clones proxied request bodies into a PassThrough so
  > middleware/rewrites can read them, and caps that buffer at 10 MB by
  > default. Anything bigger is truncated mid-stream and the upstream
  > connection is closed (the client sees ECONNRESET, the dev console logs
  > 'Request body exceeded 10MB'). Our `/api/ml/v1/documents/upload` route
  > has to accept multi-GB CSVs, so we lift the limit high enough to cover
  > them."

- **`/api/ml/:path*` rewrite stays a rewrite, not a route handler**
  (`apps/web/next.config.ts:27-33`)
  > "The `/api/ml/*` rewrite proxies multipart bodies straight through to
  > the FastAPI service. If you ever consider moving the upload behind a
  > Next.js route handler (`app/api/.../route.ts`) or server action,
  > remember that those default to buffering the entire request body in
  > memory, which will OOM the dev server on big files. Keep large uploads
  > on this rewrite path (and keep `middlewareClientMaxBodySize` above
  > generous)."

### Migration notes

- **Option name has changed across Next.js versions**
  (`apps/web/next.config.ts:14-17`)
  > "The option is named `middlewareClientMaxBodySize` in 15.5.x (it was
  > renamed to `proxyClientMaxBodySize` in newer versions — bump both if
  > you upgrade Next.js). Value is parsed by the `bytes` package, so
  > 'b/kb/mb/gb' all work."

  When upgrading Next.js, double-check this key name; if both keys are
  unrecognized, large uploads silently regress to the default 10 MB cap.

### Warnings

- **V8 heap headroom for very large uploads**
  (`apps/web/next.config.ts:19-24`)
  > "Trade-off: Next.js holds the body in V8 heap as it streams through.
  > For files larger than a few hundred MB you may also need to bump
  > Node's heap with `NODE_OPTIONS=--max-old-space-size=4096` (or higher).
  > If you regularly ingest >2 GB files, prefer pointing the browser
  > straight at `http://127.0.0.1:8000/v1/documents/upload` to bypass this
  > proxy entirely."

---

## `apps/web/app/layout.tsx`, `app/page.tsx`

No narrative comments to migrate. `page.tsx` redirects `/` to `/explore`;
`layout.tsx` mounts the `ProjectProvider`, the global nav, and `globals.css`.

---

## `apps/web/app/evaluation/page.tsx`

### Assumptions

- **Older API builds may omit `text` on rows**
  (`apps/web/app/evaluation/page.tsx:34-35`)
  > "Full document body. Older API builds may not include it, so handle
  > absence by falling back to `text_preview` in the renderer."

  The `EvaluationRow.text?:` field is optional in the type for that reason;
  `DocumentText` reads `row.text ?? row.text_preview`.

### Decisions

- **Reset selected tag/run on project switch**
  (`apps/web/app/evaluation/page.tsx:138`)
  > "Reset selected tag/run on project switch."

  Otherwise the dropdowns retain ids that belong to the previous project and
  the next fetch returns 404. Same pattern in `app/studio/page.tsx`.

### Notes

- **Rows truncated; summary metrics are not**
  (`apps/web/app/evaluation/page.tsx:418-421`)
  > "The list of rows was truncated. Summary metrics above are exact; only
  > the per-document listing is capped."

  Mirrors the API contract documented in
  [`docs/notes-ml.md`](./notes-ml.md) → `routers/evaluation.py`.

---

## `apps/web/app/explore/page.tsx`

### Decisions

- **Reset to first page when filters change**
  (`apps/web/app/explore/page.tsx:51-52`)
  > "Reset to the first page whenever filters or page size change. The user
  > is looking at a different result set, so jumping to the middle would be
  > confusing."

- **XHR (not `fetch`) for upload progress + manual `project_id` injection**
  (`apps/web/app/explore/page.tsx:240-242`)
  > "`mlFetch` normally injects `project_id` into the URL and form body,
  > but we need XHR (not fetch) to get upload progress events for multi-GB
  > files."

  The route also passes `project_id` as a query parameter on the URL; both
  paths are accepted server-side (see `services/ml/app/routers/documents.py`
  → "Project id accepted from form OR query").

---

## `apps/web/app/studio/page.tsx`

### Decisions

- **Reset tag and downstream state on project switch**
  (`apps/web/app/studio/page.tsx:84-85`)
  > "Reset selected tag when switching projects so we don't keep a stale id
  > pointing at a tag from another project."

  Drops `selectedTagId`, `lfs`, `selectedLfIds`, `lastRun`, and `matrix`.

### Future enhancements

- **Synchronous batch run is an MVP shortcut**
  (`apps/web/app/studio/page.tsx:341-342`)
  > "Select LFs to include, then execute over the full corpus. Runs are
  > synchronous on the API for the MVP."

  Long runs block the request thread; the eventual upgrade is a job queue
  with a status poll endpoint.

---

## `apps/web/app/projects/page.tsx`

### Notes

- **Per-project count fetches are best-effort**
  (`apps/web/app/projects/page.tsx:41-42`)
  > "ignore single failures"

  Failing to load counts for one project must not blank the whole list; the
  cell renders `—` and the rest of the page keeps working.

---

## `apps/web/components/NoProjectGate.tsx`

### Decisions

- **When `NoProjectGate` returns null vs the warning panel**
  (`apps/web/components/NoProjectGate.tsx:7-13`)
  > "Render an instructional empty state when there is no active project.
  > Pages that depend on project-scoped data should mount this above their
  > content and skip their own data fetches when the user has no project
  > selected. Returns `null` while the project list is still loading so we
  > don't flash the empty state during initial hydration."

  The "loading and project list empty" branch returns a plain "Loading
  projects…" indicator instead of the amber warning so a fresh tab doesn't
  flicker.

---

## `apps/web/components/ProjectSelector.tsx`

### Decisions

- **`router.refresh()` after switching projects**
  (`apps/web/components/ProjectSelector.tsx:33-34`)
  > "Force every page that reads project-scoped data to refetch."

  Project-scoped pages also re-run their effects on `projectId` change, but
  `router.refresh()` covers anything that lives in a server component or in
  caches that don't observe the context directly.

---

## `apps/web/lib/api.ts`

### Decisions

- **`projectScopeMiddleware` injects `project_id` for every scoped path**
  (`apps/web/lib/api.ts:8-21`)
  > "Append the active project id to every `/api/ml` request as a
  > `project_id` query parameter so each page doesn't have to plumb it
  > through manually. Project scoping is mandatory; this middleware throws
  > `MissingProjectError` for scoped routes when no project is active
  > rather than dispatching a request the backend will reject with 400."

- **`/v1/projects` is exempt — it manages projects themselves**
  (`apps/web/lib/api.ts:27`)
  > "The projects router manages projects themselves; never scope it."

  The matching exemption lives in
  [`apps/web/lib/ml-fetch.ts`](#appsweblibml-fetchts) `SCOPED_EXEMPT_PATHS`.

### Warnings

- **Why we rebuild the `Request` (Chrome `duplex`)**
  (`apps/web/lib/api.ts:16-21`)
  > "Implementation note: we can't mutate `request.url`, so we have to
  > build a new Request with the rewritten URL. We rebuild the body
  > explicitly from an `ArrayBuffer` (rather than passing the original
  > Request as init), because cloning a Request that has a JSON body forces
  > the body through a `ReadableStream` and Chrome then requires
  > `duplex: 'half'`, which surfaces as `TypeError: Failed to fetch` and
  > breaks every POST."

  If you simplify this middleware, run a POST in Chrome before merging.

---

## `apps/web/lib/ml-fetch.ts`

### Decisions

- **Mandatory project scoping, fail-fast in the client**
  (`apps/web/lib/ml-fetch.ts:1-12`)
  > "`mlFetch` wraps `fetch` so every request to `/api/ml` automatically
  > carries the active project id (read from localStorage via the
  > `ProjectContext`). This keeps every page from having to plumb
  > `project_id` through its own URL building. Project scoping is
  > **mandatory**: there is no implicit Default project on the backend
  > anymore. If a scoped request is attempted with no active project, this
  > helper throws `MissingProjectError` synchronously (well, in the
  > returned promise) so callers can render a clear 'pick or create a
  > project' empty state instead of dispatching a request the API will
  > reject with 400."

- **Exempt path list mirrors the openapi-fetch middleware**
  (`apps/web/lib/ml-fetch.ts:18-20`)
  > "Endpoints that don't require — and must not receive — a `project_id`
  > query parameter. The `/v1/projects` router manages the projects
  > themselves."

- **`FormData` uploads also receive `project_id` as a form field**
  (`apps/web/lib/ml-fetch.ts:45-46`)
  > "For multipart uploads (FormData) we also append it as a form field so
  > FastAPI can read it from the form schema."

  Pairs with `routers/documents.py` accepting `project_id` from either form
  body or query.

---

## `apps/web/lib/ml-fetch-error.ts`

### Assumptions

- **What `"Failed to fetch"` actually means here**
  (`apps/web/lib/ml-fetch-error.ts:1-3`)
  > "When the Next.js rewrite cannot reach the ML service on port 8000,
  > `fetch` rejects with a TypeError whose message is 'Failed to fetch'."

  We map that one specific message to a friendly "is the API running?"
  hint so users don't bounce off a generic browser error.

---

## `apps/web/lib/project-context.tsx`

### Decisions

- **Module-level `_activeProjectId` for non-React callers**
  (`apps/web/lib/project-context.tsx:42-43`)
  > "Read the active project from module-level state. Used by `mlFetch`
  > and the openapi-fetch middleware to scope requests without prop
  > drilling."

  React owns the source of truth via `ProjectProvider`, but `mlFetch` runs
  outside the component tree (e.g. inside event handlers), so we keep a
  cached copy in module scope and a `localStorage` fallback for cold reads.

### Notes

- **`hasActiveProject` is the gate flag**
  (`apps/web/lib/project-context.tsx:24-27`)
  > "True iff the user has an active project that exists in the loaded
  > list. Pages should gate their data fetches on this so they don't fire
  > requests the backend will reject with 400 ('project_id is required')."

  Pattern: every page reads `hasActiveProject` from `useProject()` and
  renders `<NoProjectGate />` when it's false, only kicking off effects
  once it's true.

---

## Configs (`.eslintrc.json`, `package.json`, `postcss.config.mjs`, `tailwind.config.ts`, `tsconfig.json`, `next-env.d.ts`, `app/globals.css`)

No narrative comments to migrate. Notable points captured here for
completeness:

- `apps/web/next-env.d.ts` is regenerated by Next.js on every `next dev` /
  `next build` (the file itself contains the directive *"This file should
  not be edited"*). It is committed to git today; many Next.js projects
  gitignore it. No-op for the cleanup pass.
- `apps/web/tsconfig.tsbuildinfo` was the TypeScript incremental build
  cache; deleted as part of this cleanup and added to `.gitignore` via
  `*.tsbuildinfo`.

---

## Cross-cutting issue fixed during cleanup

The Python-flavored `lib/` rule on line 17 of root `.gitignore` was
unanchored, so it matched `apps/web/lib/` too — leaving four essential
frontend modules invisible to git:

- `apps/web/lib/api.ts`
- `apps/web/lib/ml-fetch.ts`
- `apps/web/lib/ml-fetch-error.ts`
- `apps/web/lib/project-context.tsx`

The rule was changed to `/lib/` (root-anchored) so `apps/web/lib/` is now
trackable. Run `git add apps/web/lib/` to bring them in.
