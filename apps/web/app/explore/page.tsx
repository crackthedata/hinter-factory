"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { components } from "@hinter/contracts";

import { api } from "@/lib/api";
import { describeMlFetchError } from "@/lib/ml-fetch-error";
import { mlFetch } from "@/lib/ml-fetch";
import { useProject } from "@/lib/project-context";
import { NoProjectGate } from "@/components/NoProjectGate";

type Document = components["schemas"]["Document"];
type LengthBucket = components["schemas"]["LengthBucket"];
type Tag = components["schemas"]["Tag"];
type LfVote = components["schemas"]["LfVote"];
type GoldLabel = components["schemas"]["GoldLabel"];
type LabelPriorityMode = components["schemas"]["LabelPriorityMode"];
type LabelPriorityRow = components["schemas"]["LabelPriorityRow"];
type LabelPriorityResponse = components["schemas"]["LabelPriorityResponse"];
type CoverageStatsResponse = components["schemas"]["CoverageStatsResponse"];

type PriorityModeChoice = "off" | LabelPriorityMode;

export default function ExplorePage() {
  const { projectId, hasActiveProject } = useProject();
  const [q, setQ] = useState("");
  const [buckets, setBuckets] = useState<LengthBucket[]>([]);
  const [metaKey, setMetaKey] = useState("");
  const [metaValue, setMetaValue] = useState("");
  const [metaKeys, setMetaKeys] = useState<string[]>([]);
  const [metaValues, setMetaValues] = useState<string[]>([]);
  const [rows, setRows] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [csvTextColumn, setCsvTextColumn] = useState("text");
  const [csvIdColumn, setCsvIdColumn] = useState("");
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ loaded: number; total: number } | null>(
    null,
  );
  const [expandedTextIds, setExpandedTextIds] = useState<Set<string>>(() => new Set());
  const [tags, setTags] = useState<Tag[]>([]);
  const [tagsLoadError, setTagsLoadError] = useState<string | null>(null);
  const [goldTagId, setGoldTagId] = useState("");
  const [goldByDocId, setGoldByDocId] = useState<Partial<Record<string, LfVote>>>({});
  const [goldSavingDocId, setGoldSavingDocId] = useState<string | null>(null);
  const [goldMsg, setGoldMsg] = useState<string | null>(null);
  const [pageSize, setPageSize] = useState(50);
  const [pageIndex, setPageIndex] = useState(0);
  const [priorityMode, setPriorityMode] = useState<PriorityModeChoice>("off");
  const [priorityMeta, setPriorityMeta] = useState<
    Record<string, { vote_sum: number; vote_count: number; votes: LabelPriorityRow["votes"] }>
  >({});
  const [coverage, setCoverage] = useState<CoverageStatsResponse | null>(null);
  const [coverageLoading, setCoverageLoading] = useState(false);

  const PAGE_SIZE_OPTIONS = [25, 50, 100, 200, 500] as const;

  useEffect(() => {
    setPageIndex(0);
  }, [q, buckets, metaKey, metaValue, pageSize, projectId, priorityMode, goldTagId]);

  // When the user clears the tag selector (or the project changes), drop any
  // tag-scoped state so we don't render stale votes/coverage.
  useEffect(() => {
    if (!goldTagId) {
      setPriorityMode("off");
      setPriorityMeta({});
      setCoverage(null);
    }
  }, [goldTagId, projectId]);

  const queryString = useMemo(() => {
    const sp = new URLSearchParams();
    if (q.trim()) sp.set("q", q.trim());
    for (const b of buckets) sp.append("length_bucket", b);
    if (metaKey && metaValue) {
      sp.set("metadata_key", metaKey);
      sp.set("metadata_value", metaValue);
    }
    sp.set("limit", String(pageSize));
    sp.set("offset", String(pageIndex * pageSize));
    return sp.toString();
  }, [q, buckets, metaKey, metaValue, pageSize, pageIndex]);

  const inPriorityMode = priorityMode !== "off" && !!goldTagId;

  const refresh = useCallback(async () => {
    if (!projectId) {
      setRows([]);
      setTotal(0);
      setPriorityMeta({});
      return;
    }
    setLoading(true);
    setError(null);
    try {
      if (inPriorityMode) {
        const sp = new URLSearchParams();
        sp.set("tag_id", goldTagId);
        sp.set("mode", priorityMode);
        if (q.trim()) sp.set("q", q.trim());
        for (const b of buckets) sp.append("length_bucket", b);
        if (metaKey && metaValue) {
          sp.set("metadata_key", metaKey);
          sp.set("metadata_value", metaValue);
        }
        sp.set("limit", String(pageSize));
        sp.set("offset", String(pageIndex * pageSize));
        const res = await mlFetch(`/api/ml/v1/documents/label-priority?${sp.toString()}`);
        if (!res.ok) {
          setError("Failed to load Smart-pick documents");
          setRows([]);
          setTotal(0);
          setPriorityMeta({});
        } else {
          const data = (await res.json()) as LabelPriorityResponse;
          // LabelPriorityRow is a structural superset of Document for the
          // fields we render, so we can hoist it directly into `rows`.
          setRows(
            data.items.map((r) => ({
              id: r.id,
              text: r.text,
              metadata: r.metadata,
              char_length: r.char_length,
              created_at: r.created_at,
            })),
          );
          setTotal(data.total);
          const meta: Record<
            string,
            { vote_sum: number; vote_count: number; votes: LabelPriorityRow["votes"] }
          > = {};
          for (const r of data.items) {
            meta[r.id] = { vote_sum: r.vote_sum, vote_count: r.vote_count, votes: r.votes };
          }
          setPriorityMeta(meta);
          if (data.message) setError(data.message);
        }
      } else {
        const res = await mlFetch(`/api/ml/v1/documents?${queryString}`);
        if (!res.ok) {
          setError("Failed to load documents");
          setRows([]);
          setTotal(0);
        } else {
          const data = (await res.json()) as { items: Document[]; total: number };
          setRows(data.items);
          setTotal(data.total);
          setPriorityMeta({});
        }
      }
    } catch (e) {
      setRows([]);
      setTotal(0);
      setPriorityMeta({});
      setError(describeMlFetchError(e));
    } finally {
      setLoading(false);
    }
  }, [
    queryString,
    projectId,
    inPriorityMode,
    goldTagId,
    priorityMode,
    q,
    buckets,
    metaKey,
    metaValue,
    pageSize,
    pageIndex,
  ]);

  useEffect(() => {
    void refresh();
  }, [refresh, projectId]);

  // Coverage banner: refresh whenever the user picks a different tag, after a
  // labeling session (we re-pull when rows change so the banner stays honest
  // about how many sample docs already have gold).
  const refreshCoverage = useCallback(async () => {
    if (!projectId || !goldTagId) {
      setCoverage(null);
      return;
    }
    setCoverageLoading(true);
    try {
      const res = await mlFetch(
        `/api/ml/v1/documents/coverage-stats?tag_id=${encodeURIComponent(goldTagId)}&sample_size=200`,
      );
      if (!res.ok) {
        setCoverage(null);
      } else {
        setCoverage((await res.json()) as CoverageStatsResponse);
      }
    } catch {
      setCoverage(null);
    } finally {
      setCoverageLoading(false);
    }
  }, [projectId, goldTagId]);

  useEffect(() => {
    void refreshCoverage();
  }, [refreshCoverage]);

  useEffect(() => {
    if (!projectId) {
      setTags([]);
      return;
    }
    void (async () => {
      try {
        const { data } = await api.GET("/v1/tags", {});
        if (data) {
          setTags(data);
          setTagsLoadError(null);
        }
      } catch (e) {
        setTagsLoadError(describeMlFetchError(e));
      }
    })();
  }, [projectId]);

  useEffect(() => {
    if (!projectId) {
      setMetaKeys([]);
      return;
    }
    void (async () => {
      try {
        const { data } = await api.GET("/v1/documents/facets/metadata-keys", {});
        if (data) setMetaKeys(data);
      } catch {
        /* facets optional */
      }
    })();
  }, [projectId]);

  useEffect(() => {
    if (!goldTagId || !rows.length) {
      setGoldByDocId({});
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const sp = new URLSearchParams();
        sp.set("tag_id", goldTagId);
        for (const r of rows) sp.append("document_ids", r.id);
        const res = await mlFetch(`/api/ml/v1/gold-labels?${sp.toString()}`);
        if (!res.ok) {
          if (!cancelled) setGoldMsg("Could not load gold labels for this page.");
          return;
        }
        const list = (await res.json()) as GoldLabel[];
        if (cancelled) return;
        const next: Partial<Record<string, LfVote>> = {};
        for (const g of list) next[g.document_id] = g.value;
        setGoldByDocId(next);
        setGoldMsg(null);
      } catch (e) {
        if (!cancelled) setGoldMsg(describeMlFetchError(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [goldTagId, rows]);

  useEffect(() => {
    if (!metaKey) {
      setMetaValues([]);
      return;
    }
    void (async () => {
      const { data } = await api.GET("/v1/documents/facets/metadata-values", {
        params: { query: { key: metaKey, limit: 200 } },
      });
      if (data) setMetaValues(data);
    })();
  }, [metaKey]);

  const toggleBucket = (b: LengthBucket) => {
    setBuckets((prev) => (prev.includes(b) ? prev.filter((x) => x !== b) : [...prev, b]));
  };

  const toggleTextExpanded = (id: string) => {
    setExpandedTextIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allExpanded = rows.length > 0 && rows.every((r) => expandedTextIds.has(r.id));
  const setAllExpanded = (expand: boolean) => {
    setExpandedTextIds(expand ? new Set(rows.map((r) => r.id)) : new Set());
  };

  const TEXT_PREVIEW_CHARS = 220;

  const setGoldVote = async (documentId: string, value: LfVote) => {
    if (!goldTagId) return;
    setGoldMsg(null);
    const prev = goldByDocId[documentId];
    setGoldByDocId((m) => ({ ...m, [documentId]: value }));
    setGoldSavingDocId(documentId);
    const { error } = await api.POST("/v1/gold-labels", {
      body: { document_id: documentId, tag_id: goldTagId, value },
    });
    setGoldSavingDocId(null);
    if (error) {
      setGoldByDocId((m) => {
        const next = { ...m };
        if (prev === undefined) delete next[documentId];
        else next[documentId] = prev;
        return next;
      });
      setGoldMsg("Could not save gold label.");
    } else if (inPriorityMode) {
      // The doc just left the unlabeled pool; pull a fresh page so the next
      // priority candidate slides into view. Coverage can stay (sample_with_gold
      // is the only thing that changes and the banner refresh below covers it).
      void refresh();
      void refreshCoverage();
    }
  };

  const onUpload = async () => {
    const file = pendingFile;
    if (!file) {
      setUploadMsg("Choose a file first.");
      return;
    }
    if (!projectId) {
      setUploadMsg("Pick a project first.");
      return;
    }
    setUploadMsg(null);
    setUploading(true);
    setUploadProgress({ loaded: 0, total: file.size });

    const fd = new FormData();
    fd.append("file", file);
    const textCol = csvTextColumn.trim() || "text";
    fd.append("text_column", textCol);
    const idCol = csvIdColumn.trim();
    if (idCol) fd.append("id_column", idCol);
    // See docs/notes-web.md (apps/web/app/explore/page.tsx section): XHR + manual project_id injection for upload progress.
    fd.append("project_id", projectId);

    type UploadOk = {
      inserted?: number;
      skipped?: number;
      errors?: string[];
      truncated_errors_count?: number;
    };
    type UploadErr = { detail?: unknown };

    try {
      const result = await new Promise<{ status: number; body: UploadOk & UploadErr }>(
        (resolve, reject) => {
          const xhr = new XMLHttpRequest();
          const url = `/api/ml/v1/documents/upload?project_id=${encodeURIComponent(projectId)}`;
          xhr.open("POST", url, true);
          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
              setUploadProgress({ loaded: e.loaded, total: e.total });
            }
          };
          xhr.onload = () => {
            let parsed: unknown = null;
            try {
              parsed = JSON.parse(xhr.responseText || "null");
            } catch {
              /* leave null */
            }
            resolve({
              status: xhr.status,
              body: (parsed ?? {}) as UploadOk & UploadErr,
            });
          };
          xhr.onerror = () => reject(new Error("Network error during upload"));
          xhr.onabort = () => reject(new Error("Upload aborted"));
          xhr.send(fd);
        },
      );

      if (result.status < 200 || result.status >= 300) {
        let detail = `Upload failed (HTTP ${result.status}).`;
        const d = result.body.detail;
        if (typeof d === "string") {
          detail = d;
        } else if (Array.isArray(d)) {
          detail = d
            .map((e: unknown) =>
              typeof e === "object" && e && "msg" in e
                ? String((e as { msg: string }).msg)
                : JSON.stringify(e),
            )
            .join("; ");
        }
        setUploadMsg(detail);
      } else {
        const inserted = result.body.inserted ?? 0;
        const updated = result.body.skipped ?? 0;
        const warnings = (result.body.errors ?? []).length;
        const dropped = result.body.truncated_errors_count ?? 0;
        const warnSuffix =
          dropped > 0 ? `${warnings + dropped} row warnings (showing ${warnings})` : `${warnings} row warnings`;
        setUploadMsg(`Inserted ${inserted}, updated ${updated}. ${warnSuffix}.`);
        setPendingFile(null);
        await refresh();
      }
    } catch (e) {
      setUploadMsg(describeMlFetchError(e));
    } finally {
      setUploading(false);
      setUploadProgress(null);
    }
  };

  if (!hasActiveProject) {
    return (
      <div className="space-y-8">
        <div>
          <h1 className="text-2xl font-semibold text-white">Explore</h1>
          <p className="mt-2 max-w-2xl text-sm text-ink-500">
            Ingest a CSV/JSON corpus, then search and facet by length buckets and top-level JSON
            metadata fields.
          </p>
        </div>
        <NoProjectGate pageName="Explore" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-white">Explore</h1>
        <p className="mt-2 max-w-2xl text-sm text-ink-500">
          Ingest a CSV/JSON corpus, then search and facet by length buckets and top-level JSON metadata fields. Set
          manual gold labels (+1 / 0 / −1 per tag, same semantics as labeling functions) for documents on this page.
        </p>
      </div>

      <section className="rounded-lg border border-ink-900 bg-ink-900/30 p-4">
        <div className="text-sm font-medium text-white">Ingest</div>

        {pendingFile && pendingFile.name.toLowerCase().endsWith(".json") ? (
          <div className="mt-2 space-y-2">
            <p className="text-xs text-ink-500">
              JSON files must be an <span className="text-ink-200">array of objects</span> (or{" "}
              <span className="font-mono text-ink-200">{`{"documents": [...]}`}</span>). Each object
              must have a <span className="font-mono text-ink-200">"text"</span> key containing the
              document body. An optional <span className="font-mono text-ink-200">"id"</span> key
              sets a stable ID; all other keys are stored as metadata.
            </p>
            <pre className="overflow-x-auto rounded-md border border-ink-800 bg-ink-950 p-3 text-[11px] leading-relaxed text-ink-300">
{`[
  { "id": "doc-1", "text": "Invoice #42 for consulting services.", "source": "email" },
  { "id": "doc-2", "text": "Your receipt is attached.", "source": "web" }
]`}
            </pre>
          </div>
        ) : (
          <>
            <p className="mt-1 text-xs text-ink-500">
              For CSV, set <span className="text-ink-200">Text column</span> to the header that
              holds each document&apos;s body (match is case-insensitive). Any other columns are
              stored as JSON metadata.
            </p>
            <div className="mt-3 grid max-w-xl gap-3 sm:grid-cols-2">
              <label className="block text-xs text-ink-500">
                Text column (CSV header)
                <input
                  className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 font-mono text-sm text-white outline-none ring-accent-500 focus:ring-2"
                  value={csvTextColumn}
                  onChange={(e) => setCsvTextColumn(e.target.value)}
                  placeholder="text"
                  spellCheck={false}
                />
              </label>
              <label className="block text-xs text-ink-500">
                Id column (optional)
                <input
                  className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 font-mono text-sm text-white outline-none ring-accent-500 focus:ring-2"
                  value={csvIdColumn}
                  onChange={(e) => setCsvIdColumn(e.target.value)}
                  placeholder="e.g. id or file"
                  spellCheck={false}
                />
              </label>
            </div>
          </>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input
            className="block w-full max-w-md text-sm text-ink-200 file:mr-4 file:rounded-md file:border-0 file:bg-accent-600 file:px-3 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-accent-500"
            type="file"
            accept=".csv,.json"
            onChange={(e) => {
              setPendingFile(e.target.files?.[0] ?? null);
              setUploadMsg(null);
            }}
          />
          <button
            type="button"
            disabled={!pendingFile || uploading}
            onClick={() => void onUpload()}
            className="rounded-md bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {uploading ? "Uploading…" : "Upload"}
          </button>
          {pendingFile ? (
            <span className="text-xs text-ink-500">
              Ready: <span className="text-ink-200">{pendingFile.name}</span>
            </span>
          ) : null}
        </div>
        {uploading && uploadProgress ? (
          <div className="mt-3 max-w-md">
            <progress
              className="h-2 w-full overflow-hidden rounded bg-ink-800 [&::-webkit-progress-bar]:bg-ink-800 [&::-webkit-progress-value]:bg-accent-500 [&::-moz-progress-bar]:bg-accent-500"
              value={uploadProgress.loaded}
              max={uploadProgress.total || 1}
            />
            <div className="mt-1 flex justify-between text-[11px] text-ink-500">
              <span>
                {formatBytes(uploadProgress.loaded)} / {formatBytes(uploadProgress.total)}
              </span>
              <span>
                {uploadProgress.total
                  ? `${Math.round((uploadProgress.loaded / uploadProgress.total) * 100)}%`
                  : ""}
              </span>
            </div>
          </div>
        ) : null}
        {uploadMsg ? <div className="mt-2 text-xs text-ink-200">{uploadMsg}</div> : null}
      </section>

      <section className="space-y-4 rounded-lg border border-ink-900 bg-ink-900/30 p-4">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block text-sm">
            <div className="text-xs font-medium text-ink-500">Search</div>
            <input
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Substring match on document text"
            />
          </label>

          <div className="text-sm">
            <div className="text-xs font-medium text-ink-500">Length buckets</div>
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-ink-200">
              {(["short", "medium", "long"] as const).map((b) => (
                <label key={b} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={buckets.includes(b)}
                    onChange={() => toggleBucket(b)}
                    className="h-4 w-4 rounded border-ink-700 bg-ink-950"
                  />
                  <span>
                    {b} <span className="text-ink-500">({bucketHint(b)})</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <label className="block text-sm">
            <div className="text-xs font-medium text-ink-500">Metadata key</div>
            <select
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2"
              value={metaKey}
              onChange={(e) => {
                setMetaKey(e.target.value);
                setMetaValue("");
              }}
            >
              <option value="">(none)</option>
              {metaKeys.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <div className="text-xs font-medium text-ink-500">Metadata value</div>
            <select
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2"
              value={metaValue}
              disabled={!metaKey}
              onChange={(e) => setMetaValue(e.target.value)}
            >
              <option value="">(any)</option>
              {metaValues.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="rounded-md bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-500"
            onClick={() => void refresh()}
          >
            Refresh
          </button>
          {loading ? <span className="text-xs text-ink-500">Loading…</span> : null}
          {error ? <span className="text-xs text-red-400">{error}</span> : null}
          <span className="text-xs text-ink-500">
            {total > 0
              ? `Showing ${pageIndex * pageSize + 1}–${Math.min(
                  (pageIndex + 1) * pageSize,
                  total,
                )} of ${total}`
              : `${total} matching documents`}
          </span>
          <label className="ml-auto flex items-center gap-2 text-xs text-ink-500">
            Page size
            <select
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
              className="rounded-md border border-ink-700 bg-ink-950 px-2 py-1 text-xs text-white outline-none ring-accent-500 focus:ring-2"
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="mt-4 border-t border-ink-800 pt-4">
          <div className="text-xs font-medium text-ink-500">Manual gold label (per tag)</div>
          <p className="mt-1 text-xs text-ink-500">
            Choose a tag, then vote +1 (positive for tag), 0 (abstain), or −1 (negative). Tags are created in LF
            Studio.
          </p>
          {tagsLoadError ? <div className="mt-2 text-xs text-red-400">{tagsLoadError}</div> : null}
          <label className="mt-2 block max-w-md text-xs text-ink-500">
            Tag
            <select
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2"
              value={goldTagId}
              onChange={(e) => setGoldTagId(e.target.value)}
            >
              <option value="">(none — hide gold column)</option>
              {tags.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          {!tags.length && !tagsLoadError ? (
            <p className="mt-2 text-xs text-ink-500">No tags yet. Open LF Studio and create a tag first.</p>
          ) : null}
          {goldMsg ? <div className="mt-2 text-xs text-amber-400">{goldMsg}</div> : null}

          {goldTagId ? (
            <>
              <CoverageBanner stats={coverage} loading={coverageLoading} />
              <div className="mt-3 rounded-md border border-ink-800 bg-ink-950/60 p-3">
                <div className="text-xs font-medium text-ink-300">Smart pick</div>
                <p className="mt-1 text-xs text-ink-500">
                  Re-orders unlabeled documents for active learning. Uses the latest completed LF run for this tag.
                </p>
                <div className="mt-2 flex flex-wrap gap-2 text-xs">
                  {(
                    [
                      { id: "off", label: "Off (newest first)" },
                      { id: "uncertain", label: "Most uncertain" },
                      { id: "no_lf_fires", label: "No LFs fire" },
                      { id: "weak_positive", label: "Weak positives" },
                    ] as const
                  ).map((opt) => {
                    const active = priorityMode === opt.id;
                    return (
                      <button
                        key={opt.id}
                        type="button"
                        onClick={() => setPriorityMode(opt.id)}
                        className={`rounded-full px-3 py-1 transition-colors ${
                          active
                            ? "bg-accent-600 text-white"
                            : "border border-ink-700 bg-ink-950 text-ink-200 hover:border-accent-500"
                        }`}
                      >
                        {opt.label}
                      </button>
                    );
                  })}
                </div>
                {inPriorityMode ? (
                  <p className="mt-2 text-[11px] text-ink-500">
                    {priorityMode === "uncertain"
                      ? "Sorted by smallest |vote_sum| first — closest splits surface first."
                      : priorityMode === "no_lf_fires"
                        ? "Showing docs no LF voted on — these are the coverage holes."
                        : "Showing docs predicted positive on a single LF vote — most likely false positives."}
                  </p>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      </section>

      {rows.length ? (
        <div className="-mb-4 flex items-center justify-end gap-2 text-xs text-ink-500">
          <span>Text preview</span>
          <button
            type="button"
            onClick={() => setAllExpanded(!allExpanded)}
            className="rounded border border-ink-700 bg-ink-950 px-2 py-1 text-xs text-ink-200 hover:border-accent-500"
          >
            {allExpanded ? "Collapse all" : "Expand all"}
          </button>
        </div>
      ) : null}

      <section className="overflow-hidden rounded-lg border border-ink-900">
        <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-ink-900 text-left text-sm">
          <thead className="bg-ink-900/50 text-xs uppercase tracking-wide text-ink-500">
            <tr>
              <th className="px-3 py-2">ID</th>
              <th className="px-3 py-2">Len</th>
              {inPriorityMode ? <th className="px-3 py-2 whitespace-nowrap">Votes</th> : null}
              <th className="px-3 py-2">Metadata</th>
              <th className="w-full px-3 py-2">Text</th>
              {goldTagId ? (
                <th className="px-2 py-2 whitespace-nowrap">Gold</th>
              ) : null}
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-900">
            {rows.map((d) => {
              const expanded = expandedTextIds.has(d.id);
              const isLong = d.text.length > TEXT_PREVIEW_CHARS;
              const shown = expanded || !isLong ? d.text : `${d.text.slice(0, TEXT_PREVIEW_CHARS)}…`;
              return (
              <tr key={d.id} className="align-top">
                <td className="px-3 py-2 font-mono text-xs text-ink-500">{d.id.slice(0, 8)}…</td>
                <td className="px-3 py-2 text-xs text-ink-200">{d.char_length}</td>
                {inPriorityMode ? (
                  <td className="px-3 py-2 text-xs text-ink-200">
                    <PriorityVotesCell info={priorityMeta[d.id]} />
                  </td>
                ) : null}
                <td className="px-3 py-2 text-xs text-ink-500">
                  <pre className="max-w-xs whitespace-pre-wrap break-words">
                    {JSON.stringify(d.metadata, null, 0)}
                  </pre>
                </td>
                <td className="px-3 py-2 text-xs text-ink-200">
                  <pre
                    className={`whitespace-pre-wrap break-words font-sans ${
                      expanded ? "" : "max-h-24 overflow-hidden"
                    }`}
                  >
                    {shown}
                  </pre>
                  {isLong ? (
                    <button
                      type="button"
                      onClick={() => toggleTextExpanded(d.id)}
                      className="mt-1 text-[11px] font-medium text-accent-400 hover:text-accent-300"
                    >
                      {expanded ? "Show less" : `Show full (${d.char_length} chars)`}
                    </button>
                  ) : null}
                </td>
                {goldTagId ? (
                  <td className="px-2 py-2 align-top">
                    <div className="flex flex-wrap gap-1">
                      {([1, 0, -1] as const).map((v) => {
                        const active = goldByDocId[d.id] === v;
                        return (
                          <button
                            key={v}
                            type="button"
                            title={v === 1 ? "Positive for tag" : v === 0 ? "Abstain" : "Negative for tag"}
                            disabled={goldSavingDocId === d.id}
                            className={`rounded px-2 py-1 font-mono text-[11px] font-medium transition-colors disabled:opacity-40 ${
                              active
                                ? "bg-accent-600 text-white"
                                : "border border-ink-600 bg-ink-950 text-ink-200 hover:border-accent-500"
                            }`}
                            onClick={() => void setGoldVote(d.id, v)}
                          >
                            {v === 1 ? "+1" : v === 0 ? "0" : "−1"}
                          </button>
                        );
                      })}
                    </div>
                  </td>
                ) : null}
              </tr>
              );
            })}
            {!rows.length ? (
              <tr>
                <td
                  className="px-3 py-6 text-sm text-ink-500"
                  colSpan={4 + (goldTagId ? 1 : 0) + (inPriorityMode ? 1 : 0)}
                >
                  {inPriorityMode
                    ? "Nothing matches this Smart-pick mode. Try a different mode or clear filters."
                    : "No documents yet. Upload a CSV/JSON file to begin."}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        </div>
        {total > 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-900 px-3 py-2 text-xs text-ink-400">
            <span>
              Page {pageIndex + 1} of {Math.max(1, Math.ceil(total / pageSize))}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
                disabled={pageIndex === 0 || loading}
                className="rounded-md border border-ink-700 bg-ink-950 px-3 py-1 font-medium text-ink-100 transition-colors hover:border-accent-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                ← Prev
              </button>
              <button
                type="button"
                onClick={() =>
                  setPageIndex((p) =>
                    (p + 1) * pageSize < total ? p + 1 : p,
                  )
                }
                disabled={(pageIndex + 1) * pageSize >= total || loading}
                className="rounded-md border border-ink-700 bg-ink-950 px-3 py-1 font-medium text-ink-100 transition-colors hover:border-accent-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function CoverageBanner({
  stats,
  loading,
}: {
  stats: CoverageStatsResponse | null;
  loading: boolean;
}) {
  if (loading && !stats) {
    return (
      <div className="mt-3 rounded-md border border-ink-800 bg-ink-950/60 p-3 text-xs text-ink-500">
        Loading coverage…
      </div>
    );
  }
  if (!stats) return null;
  if (stats.message) {
    return (
      <div className="mt-3 rounded-md border border-amber-900/60 bg-amber-950/30 p-3 text-xs text-amber-200">
        {stats.message}
      </div>
    );
  }
  const ceiling = stats.estimated_recall_ceiling;
  const pct = ceiling != null ? Math.round(ceiling * 100) : null;
  const noFire = stats.sample_no_lf_fires;
  const tone =
    pct == null
      ? "border-ink-800 bg-ink-950/60 text-ink-300"
      : pct >= 80
        ? "border-emerald-900/60 bg-emerald-950/30 text-emerald-200"
        : pct >= 50
          ? "border-amber-900/60 bg-amber-950/30 text-amber-200"
          : "border-red-900/60 bg-red-950/30 text-red-200";
  return (
    <div className={`mt-3 rounded-md border p-3 text-xs ${tone}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-semibold">
          Estimated recall ceiling: {pct != null ? `${pct}%` : "—"}
        </span>
        <span className="text-ink-400">
          ({noFire} of {stats.sample_size} sampled docs match no LF · {stats.sample_with_gold}{" "}
          already gold-labeled)
        </span>
      </div>
      {pct != null && pct < 80 ? (
        <p className="mt-1 text-[11px] text-ink-400">
          Use <span className="text-ink-200">Smart pick → No LFs fire</span> below to label coverage
          holes and raise the ceiling.
        </p>
      ) : null}
    </div>
  );
}

function PriorityVotesCell({
  info,
}: {
  info?: { vote_sum: number; vote_count: number; votes: LabelPriorityRow["votes"] };
}) {
  if (!info) return <span className="text-ink-500">—</span>;
  const sumLabel = info.vote_sum > 0 ? `+${info.vote_sum}` : `${info.vote_sum}`;
  const sumTone =
    info.vote_sum > 0
      ? "text-emerald-300"
      : info.vote_sum < 0
        ? "text-red-300"
        : "text-ink-300";
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className={`font-mono text-[11px] font-semibold ${sumTone}`}>{sumLabel}</span>
        <span className="text-[10px] text-ink-500">
          {info.vote_count} {info.vote_count === 1 ? "LF" : "LFs"}
        </span>
      </div>
      {info.votes.length ? (
        <div className="flex max-w-[200px] flex-wrap gap-1">
          {info.votes.map((v, i) => (
            <span
              key={`${v.labeling_function_id}-${i}`}
              title={`${v.labeling_function_name} voted ${v.vote === 1 ? "+1" : v.vote === -1 ? "−1" : "0"}`}
              className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                v.vote > 0
                  ? "bg-emerald-900/40 text-emerald-200"
                  : v.vote < 0
                    ? "bg-red-900/40 text-red-200"
                    : "bg-ink-900/60 text-ink-300"
              }`}
            >
              {v.labeling_function_name}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function bucketHint(b: LengthBucket) {
  if (b === "short") return "<100 chars";
  if (b === "medium") return "100–499 chars";
  return "500+ chars";
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}
