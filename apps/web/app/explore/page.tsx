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

  const PAGE_SIZE_OPTIONS = [25, 50, 100, 200, 500] as const;

  useEffect(() => {
    setPageIndex(0);
  }, [q, buckets, metaKey, metaValue, pageSize, projectId]);

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

  const refresh = useCallback(async () => {
    if (!projectId) {
      setRows([]);
      setTotal(0);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await mlFetch(`/api/ml/v1/documents?${queryString}`);
      if (!res.ok) {
        setError("Failed to load documents");
        setRows([]);
        setTotal(0);
      } else {
        const data = (await res.json()) as { items: Document[]; total: number };
        setRows(data.items);
        setTotal(data.total);
      }
    } catch (e) {
      setRows([]);
      setTotal(0);
      setError(describeMlFetchError(e));
    } finally {
      setLoading(false);
    }
  }, [queryString, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh, projectId]);

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
        <p className="mt-1 text-xs text-ink-500">
          For CSV, set <span className="text-ink-200">Text column</span> to the header that holds each document&apos;s
          body (match is case-insensitive). Any other columns are stored as JSON metadata. JSON uploads ignore these
          fields.
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
        <table className="min-w-full divide-y divide-ink-900 text-left text-sm">
          <thead className="bg-ink-900/50 text-xs uppercase tracking-wide text-ink-500">
            <tr>
              <th className="px-3 py-2">ID</th>
              <th className="px-3 py-2">Len</th>
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
                  <td className="px-2 py-2 align-middle">
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
                <td className="px-3 py-6 text-sm text-ink-500" colSpan={goldTagId ? 5 : 4}>
                  No documents yet. Upload a CSV/JSON file to begin.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
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
