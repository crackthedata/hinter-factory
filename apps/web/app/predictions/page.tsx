"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { components } from "@hinter/contracts";

import { describeMlFetchError } from "@/lib/ml-fetch-error";
import { mlFetch } from "@/lib/ml-fetch";
import { useProject } from "@/lib/project-context";
import { NoProjectGate } from "@/components/NoProjectGate";

type Tag = components["schemas"]["Tag"];
type ProbabilisticLabelRow = components["schemas"]["ProbabilisticLabelRow"];
type ProbabilisticLabelListResponse =
  components["schemas"]["ProbabilisticLabelListResponse"];
type ProbabilityDistributionResponse =
  components["schemas"]["ProbabilityDistributionResponse"];

type SortOption = "probability_desc" | "probability_asc" | "entropy_desc";
type PredictedFilter = "" | "positive" | "negative" | "abstain";

const PAGE_SIZE_OPTIONS = [25, 50, 100, 200] as const;

function formatProbability(p: number): string {
  return `${(p * 100).toFixed(1)}%`;
}

function formatNumber(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

function predictedTone(predicted: number): "emerald" | "red" | "neutral" {
  if (predicted === 1) return "emerald";
  if (predicted === -1) return "red";
  return "neutral";
}

function predictedLabel(predicted: number): string {
  if (predicted === 1) return "+1";
  if (predicted === -1) return "−1";
  return "0";
}

export default function PredictionsPage() {
  const { projectId, hasActiveProject } = useProject();
  const [tags, setTags] = useState<Tag[]>([]);
  const [tagId, setTagId] = useState("");
  const [predicted, setPredicted] = useState<PredictedFilter>("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortOption>("probability_desc");
  const [pageSize, setPageSize] = useState<number>(50);
  const [pageIndex, setPageIndex] = useState(0);
  const [data, setData] = useState<ProbabilisticLabelListResponse | null>(null);
  const [distribution, setDistribution] =
    useState<ProbabilityDistributionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    setTagId("");
    setData(null);
    setDistribution(null);
  }, [projectId]);

  useEffect(() => {
    setPageIndex(0);
  }, [tagId, predicted, q, sort, pageSize, projectId]);

  useEffect(() => {
    if (!projectId) {
      setTags([]);
      return;
    }
    void (async () => {
      try {
        const res = await mlFetch("/api/ml/v1/tags");
        if (!res.ok) {
          setError("Could not load tags.");
          return;
        }
        setTags((await res.json()) as Tag[]);
      } catch (e) {
        setError(describeMlFetchError(e));
      }
    })();
  }, [projectId]);

  const queryString = useMemo(() => {
    const sp = new URLSearchParams();
    if (tagId) sp.set("tag_id", tagId);
    if (predicted) sp.set("predicted", predicted);
    if (q.trim()) sp.set("q", q.trim());
    sp.set("sort", sort);
    sp.set("limit", String(pageSize));
    sp.set("offset", String(pageIndex * pageSize));
    return sp.toString();
  }, [tagId, predicted, q, sort, pageSize, pageIndex]);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await mlFetch(`/api/ml/v1/probabilistic-labels?${queryString}`);
      if (!res.ok) {
        let detail = `Request failed (HTTP ${res.status}).`;
        try {
          const body = (await res.json()) as { detail?: string };
          if (typeof body.detail === "string") detail = body.detail;
        } catch {
          /* ignore */
        }
        setError(detail);
        setData(null);
      } else {
        const body = (await res.json()) as ProbabilisticLabelListResponse;
        setData(body);
      }
    } catch (e) {
      setError(describeMlFetchError(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [projectId, queryString]);

  const refreshDistribution = useCallback(async () => {
    if (!projectId || !tagId) {
      setDistribution(null);
      return;
    }
    try {
      const res = await mlFetch(
        `/api/ml/v1/probabilistic-labels/distribution?tag_id=${encodeURIComponent(tagId)}&bins=10`,
      );
      if (!res.ok) {
        setDistribution(null);
        return;
      }
      setDistribution((await res.json()) as ProbabilityDistributionResponse);
    } catch {
      setDistribution(null);
    }
  }, [projectId, tagId]);

  useEffect(() => {
    if (projectId) void refresh();
  }, [projectId, refresh]);

  useEffect(() => {
    void refreshDistribution();
  }, [refreshDistribution]);

  const toggleExpanded = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (!hasActiveProject) {
    return (
      <div className="space-y-8">
        <div>
          <h1 className="text-2xl font-semibold text-white">Predictions</h1>
          <p className="mt-2 max-w-2xl text-sm text-ink-500">
            Hinter Factory&apos;s confidence in every document&apos;s tag assignment, computed
            from the latest LF run.
          </p>
        </div>
        <NoProjectGate pageName="Predictions" />
      </div>
    );
  }

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const maxBin = distribution
    ? Math.max(1, ...distribution.bins.map((b) => b.count))
    : 1;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-white">Predictions</h1>
        <p className="mt-2 max-w-2xl text-sm text-ink-500">
          Probability that each document carries the selected tag. Computed at the end of every
          LF run by a Laplace-smoothed majority vote over the firing LFs:{" "}
          <code className="rounded bg-ink-900 px-1 py-0.5 text-[11px] text-ink-200">
            P = (1 + positive_votes) / (2 + positive_votes + negative_votes)
          </code>
          . With no LF firing, probability is 0.5 — &ldquo;no information.&rdquo;
        </p>
      </div>

      <section className="grid gap-4 rounded-lg border border-ink-900 bg-ink-900/30 p-4 sm:grid-cols-3 lg:grid-cols-6">
        <label className="block text-xs text-ink-500 sm:col-span-2">
          Tag
          <select
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2"
            value={tagId}
            onChange={(e) => setTagId(e.target.value)}
          >
            <option value="">(all tags)</option>
            {tags.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-ink-500">
          Predicted
          <select
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2"
            value={predicted}
            onChange={(e) => setPredicted(e.target.value as PredictedFilter)}
          >
            <option value="">All</option>
            <option value="positive">+1 (P &gt; 0.5)</option>
            <option value="abstain">0 (P = 0.5)</option>
            <option value="negative">−1 (P &lt; 0.5)</option>
          </select>
        </label>
        <label className="block text-xs text-ink-500">
          Sort
          <select
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortOption)}
          >
            <option value="probability_desc">Probability (high → low)</option>
            <option value="probability_asc">Probability (low → high)</option>
            <option value="entropy_desc">Entropy (most uncertain first)</option>
          </select>
        </label>
        <label className="block text-xs text-ink-500 sm:col-span-2">
          Search text
          <input
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2"
            placeholder="filter by document content…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        {error ? (
          <div className="text-xs text-red-400 sm:col-span-3 lg:col-span-6">{error}</div>
        ) : null}
      </section>

      {distribution && distribution.total > 0 ? (
        <section className="rounded-lg border border-ink-900 bg-ink-900/20 p-4">
          <div className="grid gap-4 sm:grid-cols-4">
            <Counter
              label="Documents scored"
              value={distribution.total}
              sub={`mean P = ${formatNumber(distribution.mean_probability ?? null, 3)}`}
            />
            <Counter
              label="Predicted +1"
              value={distribution.predicted_positive}
              accent="emerald"
            />
            <Counter
              label="Predicted 0"
              value={distribution.predicted_abstain}
              sub="no LF fired or perfectly split"
            />
            <Counter
              label="Predicted −1"
              value={distribution.predicted_negative}
              accent="red"
            />
          </div>
          <div className="mt-4">
            <div className="text-xs uppercase tracking-wide text-ink-500">
              Probability histogram
            </div>
            <div className="mt-2 flex h-24 items-end gap-1">
              {distribution.bins.map((b, i) => {
                const h = (b.count / maxBin) * 100;
                return (
                  <div
                    key={i}
                    className="group relative flex-1"
                    title={`${b.lower.toFixed(1)} ≤ P < ${b.upper.toFixed(1)} : ${b.count} docs`}
                  >
                    <div
                      className="w-full rounded-t bg-accent-500/70 transition-all group-hover:bg-accent-400"
                      style={{ height: `${h}%`, minHeight: b.count > 0 ? "2px" : "0" }}
                    />
                  </div>
                );
              })}
            </div>
            <div className="mt-1 flex justify-between text-[10px] text-ink-500">
              <span>0.0</span>
              <span>0.5</span>
              <span>1.0</span>
            </div>
          </div>
        </section>
      ) : tagId ? (
        <div className="rounded-md border border-ink-800 bg-ink-900/40 p-4 text-sm text-ink-200">
          No probabilities have been computed for this tag yet. Run the tag&apos;s LFs in{" "}
          <a className="text-accent-400 hover:text-accent-300" href="/studio">
            LF Studio
          </a>{" "}
          to populate the table.
        </div>
      ) : null}

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-3 text-xs text-ink-500">
          <span>
            {loading
              ? "Loading…"
              : `${total.toLocaleString()} document${total === 1 ? "" : "s"}`}
          </span>
          <span className="ml-auto flex items-center gap-2">
            Page size
            <select
              className="rounded border border-ink-700 bg-ink-950 px-2 py-1 text-ink-200"
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
            >
              {PAGE_SIZE_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </span>
        </div>

        <div className="space-y-2">
          {items.map((row) => (
            <PredictionRow
              key={`${row.document_id}-${row.tag_id}`}
              row={row}
              expanded={expanded.has(row.document_id)}
              onToggle={() => toggleExpanded(row.document_id)}
            />
          ))}
          {!loading && items.length === 0 ? (
            <div className="rounded-md border border-ink-800 bg-ink-900/40 p-3 text-sm text-ink-500">
              No matching documents.
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-between text-xs text-ink-500">
          <button
            type="button"
            disabled={pageIndex === 0 || loading}
            onClick={() => setPageIndex((i) => Math.max(0, i - 1))}
            className="rounded border border-ink-700 bg-ink-950 px-3 py-1 text-ink-200 hover:border-accent-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            ← Previous
          </button>
          <span>
            Page {pageIndex + 1} / {pageCount}
          </span>
          <button
            type="button"
            disabled={pageIndex + 1 >= pageCount || loading}
            onClick={() => setPageIndex((i) => i + 1)}
            className="rounded border border-ink-700 bg-ink-950 px-3 py-1 text-ink-200 hover:border-accent-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next →
          </button>
        </div>
      </section>
    </div>
  );
}

function PredictionRow({
  row,
  expanded,
  onToggle,
}: {
  row: ProbabilisticLabelRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const tone = predictedTone(row.predicted);
  const probPct = Math.round(row.probability * 100);
  const barColor =
    tone === "emerald"
      ? "bg-emerald-500"
      : tone === "red"
        ? "bg-red-500"
        : "bg-ink-500";
  const borderColor =
    tone === "emerald"
      ? "border-emerald-500/30"
      : tone === "red"
        ? "border-red-500/30"
        : "border-ink-800";

  const [fullText, setFullText] = useState<string | null>(null);
  const fetchedRef = useRef(false);

  useEffect(() => {
    if (!expanded || fetchedRef.current) return;
    fetchedRef.current = true;
    void (async () => {
      try {
        const res = await mlFetch(`/api/ml/v1/documents/${row.document_id}`);
        if (res.ok) {
          const body = (await res.json()) as { text: string };
          setFullText(body.text);
        }
      } catch {
        // fall back to text_preview on error
      }
    })();
  }, [expanded, row.document_id]);

  const displayText = expanded && fullText !== null ? fullText : row.text_preview;

  return (
    <div className={`rounded-md border ${borderColor} bg-ink-900/30 p-3`}>
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="font-mono text-ink-500">{row.document_id.slice(0, 8)}…</span>
        <Badge label="pred" value={predictedLabel(row.predicted)} tone={tone} />
        <span className="text-ink-500">
          P = <span className="font-mono text-ink-200">{formatProbability(row.probability)}</span>
        </span>
        <span className="text-ink-500">
          votes <span className="font-mono text-emerald-300">+{row.positive_votes}</span>
          {" / "}
          <span className="font-mono text-red-300">−{row.negative_votes}</span>
        </span>
        {row.conflict_score && row.conflict_score > 0 ? (
          <span className="text-ink-500">
            conflict{" "}
            <span className="font-mono text-amber-300">
              {formatNumber(row.conflict_score, 2)}
            </span>
          </span>
        ) : null}
        {row.entropy ? (
          <span className="text-ink-500">
            entropy{" "}
            <span className="font-mono text-ink-200">{formatNumber(row.entropy, 2)}</span>
          </span>
        ) : null}
        <button
          type="button"
          onClick={onToggle}
          className="ml-auto rounded border border-ink-700 bg-ink-950 px-2 py-1 text-[11px] text-ink-200 hover:border-accent-500"
        >
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-ink-900">
        <div
          className={`h-full transition-all ${barColor}`}
          style={{ width: `${probPct}%` }}
        />
      </div>
      <pre
        className={`mt-2 whitespace-pre-wrap break-words font-sans text-xs text-ink-200 ${
          expanded ? "" : "max-h-24 overflow-hidden"
        }`}
      >
        {displayText || "(empty document)"}
      </pre>
    </div>
  );
}

function Counter({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: number;
  sub?: string;
  accent?: "red" | "emerald";
}) {
  const color =
    accent === "red"
      ? "text-red-400"
      : accent === "emerald"
        ? "text-emerald-400"
        : "text-white";
  return (
    <div className="rounded-md border border-ink-800 bg-ink-900/30 p-3">
      <div className="text-xs uppercase tracking-wide text-ink-500">{label}</div>
      <div className={`mt-1 font-mono text-2xl ${color}`}>{value.toLocaleString()}</div>
      {sub ? <div className="mt-1 text-[11px] text-ink-500">{sub}</div> : null}
    </div>
  );
}

function Badge({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "emerald" | "red" | "neutral";
}) {
  const color =
    tone === "emerald"
      ? "border-emerald-500/50 text-emerald-300"
      : tone === "red"
        ? "border-red-500/50 text-red-300"
        : "border-ink-700 text-ink-200";
  return (
    <span className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${color}`}>
      {label} {value}
    </span>
  );
}
