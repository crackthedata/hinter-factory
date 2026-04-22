"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { components } from "@hinter/contracts";

import { describeMlFetchError } from "@/lib/ml-fetch-error";

type Tag = components["schemas"]["Tag"];

type LfVote = -1 | 0 | 1;

type EvaluationCategory =
  | "true_positive"
  | "true_negative"
  | "false_positive"
  | "false_negative"
  | "abstain_on_positive"
  | "abstain_on_negative"
  | "gold_abstain";

type EvaluationVote = {
  labeling_function_id: string;
  labeling_function_name: string;
  vote: LfVote;
};

type EvaluationRow = {
  document_id: string;
  text_preview: string;
  gold: LfVote;
  predicted: LfVote;
  vote_sum: number;
  category: EvaluationCategory;
  votes: EvaluationVote[];
};

type EvaluationSummary = {
  total_gold: number;
  considered: number;
  true_positive: number;
  true_negative: number;
  false_positive: number;
  false_negative: number;
  abstain_on_positive: number;
  abstain_on_negative: number;
  gold_abstain: number;
  precision: number | null;
  recall: number | null;
  f1: number | null;
  coverage: number | null;
};

type EvaluationResponse = {
  tag_id: string;
  run_id: string | null;
  run_completed_at?: string | null;
  summary: EvaluationSummary;
  rows: EvaluationRow[];
  truncated?: boolean;
  message?: string;
};

type LfRun = {
  id: string;
  tag_id: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  documents_scanned: number;
  votes_written: number;
};

const CATEGORY_LABELS: Record<EvaluationCategory, string> = {
  false_negative: "False negatives (gold +1, predicted ≤ 0)",
  false_positive: "False positives (gold −1, predicted +1)",
  abstain_on_positive: "Missed (gold +1, no LF fired)",
  abstain_on_negative: "Abstained (gold −1, no LF fired)",
  true_positive: "True positives",
  true_negative: "True negatives",
  gold_abstain: "Gold = 0 (excluded from metrics)",
};

const CATEGORY_ORDER: EvaluationCategory[] = [
  "false_negative",
  "false_positive",
  "abstain_on_positive",
  "abstain_on_negative",
  "true_positive",
  "true_negative",
  "gold_abstain",
];

const CATEGORY_COLOR: Record<EvaluationCategory, string> = {
  false_negative: "border-red-500/50 bg-red-500/10",
  false_positive: "border-amber-500/50 bg-amber-500/10",
  abstain_on_positive: "border-orange-500/40 bg-orange-500/10",
  abstain_on_negative: "border-ink-700 bg-ink-900/40",
  true_positive: "border-emerald-500/40 bg-emerald-500/10",
  true_negative: "border-ink-700 bg-ink-900/40",
  gold_abstain: "border-ink-800 bg-ink-900/30",
};

function formatPercent(x: number | null): string {
  if (x === null || Number.isNaN(x)) return "—";
  return `${(x * 100).toFixed(1)}%`;
}

function formatVote(v: LfVote | number): string {
  if (v === 1) return "+1";
  if (v === -1) return "−1";
  return "0";
}

export default function EvaluationPage() {
  const [tags, setTags] = useState<Tag[]>([]);
  const [tagId, setTagId] = useState("");
  const [runs, setRuns] = useState<LfRun[]>([]);
  const [runId, setRunId] = useState<string>("");
  const [data, setData] = useState<EvaluationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedDocs, setExpandedDocs] = useState<Set<string>>(() => new Set());
  const [visibleCategories, setVisibleCategories] = useState<Set<EvaluationCategory>>(
    () => new Set(["false_negative", "false_positive", "abstain_on_positive"]),
  );

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch("/api/ml/v1/tags");
        if (!res.ok) {
          setError("Could not load tags.");
          return;
        }
        const list = (await res.json()) as Tag[];
        setTags(list);
      } catch (e) {
        setError(describeMlFetchError(e));
      }
    })();
  }, []);

  useEffect(() => {
    if (!tagId) {
      setRuns([]);
      setRunId("");
      setData(null);
      return;
    }
    void (async () => {
      try {
        const res = await fetch(`/api/ml/v1/lf-runs?tag_id=${encodeURIComponent(tagId)}&limit=20`);
        if (!res.ok) {
          setRuns([]);
          return;
        }
        const list = (await res.json()) as LfRun[];
        setRuns(list);
      } catch {
        setRuns([]);
      }
    })();
  }, [tagId]);

  const refresh = useCallback(async () => {
    if (!tagId) return;
    setLoading(true);
    setError(null);
    setExpandedDocs(new Set());
    try {
      const sp = new URLSearchParams();
      sp.set("tag_id", tagId);
      if (runId) sp.set("run_id", runId);
      sp.set("limit", "500");
      const res = await fetch(`/api/ml/v1/evaluation?${sp.toString()}`);
      if (!res.ok) {
        let detail = `Evaluation failed (HTTP ${res.status}).`;
        try {
          const body = (await res.json()) as { detail?: string };
          if (typeof body.detail === "string") detail = body.detail;
        } catch {
          /* ignore */
        }
        setError(detail);
        setData(null);
      } else {
        const body = (await res.json()) as EvaluationResponse;
        setData(body);
      }
    } catch (e) {
      setError(describeMlFetchError(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [tagId, runId]);

  useEffect(() => {
    if (tagId) void refresh();
  }, [tagId, runId, refresh]);

  const summary = data?.summary;
  const allRows = data?.rows ?? [];
  const rowsByCategory = useMemo(() => {
    const map = new Map<EvaluationCategory, EvaluationRow[]>();
    for (const cat of CATEGORY_ORDER) map.set(cat, []);
    for (const r of allRows) {
      map.get(r.category)?.push(r);
    }
    return map;
  }, [allRows]);

  const toggleDoc = (id: string) => {
    setExpandedDocs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleCategory = (cat: EvaluationCategory) => {
    setVisibleCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  const selectedRunInfo = useMemo(() => {
    const id = data?.run_id ?? runId;
    return runs.find((r) => r.id === id) ?? null;
  }, [runs, runId, data?.run_id]);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-white">Evaluation</h1>
        <p className="mt-2 max-w-2xl text-sm text-ink-500">
          For the chosen tag, every document you&apos;ve gold-labeled becomes a validation example.
          Hinter Factory aggregates the LF votes from a run (sum-of-votes majority) and shows you
          where it currently disagrees with you. Errors at the top: <span className="text-red-400">false
          negatives</span> (the system missed a real positive) and <span className="text-amber-400">false
          positives</span> (the system flagged a negative as positive).
        </p>
      </div>

      <section className="grid gap-4 rounded-lg border border-ink-900 bg-ink-900/30 p-4 sm:grid-cols-3">
        <label className="block text-xs text-ink-500">
          Tag
          <select
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2"
            value={tagId}
            onChange={(e) => {
              setTagId(e.target.value);
              setRunId("");
            }}
          >
            <option value="">(select a tag)</option>
            {tags.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-ink-500">
          LF run
          <select
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white outline-none ring-accent-500 focus:ring-2 disabled:opacity-50"
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
            disabled={!tagId || !runs.length}
          >
            <option value="">(latest completed)</option>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.status} · {new Date(r.created_at).toLocaleString()} · {r.documents_scanned} docs
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-end">
          <button
            type="button"
            disabled={!tagId || loading}
            onClick={() => void refresh()}
            className="w-full rounded-md bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {loading ? "Evaluating…" : "Refresh"}
          </button>
        </div>
        {selectedRunInfo ? (
          <div className="text-xs text-ink-500 sm:col-span-3">
            Evaluating run <span className="font-mono text-ink-200">{selectedRunInfo.id.slice(0, 8)}…</span>{" "}
            {selectedRunInfo.completed_at
              ? `completed ${new Date(selectedRunInfo.completed_at).toLocaleString()}`
              : "(not completed)"}
            {" · "}
            {selectedRunInfo.documents_scanned} docs scanned, {selectedRunInfo.votes_written} votes written.
          </div>
        ) : null}
        {error ? <div className="text-xs text-red-400 sm:col-span-3">{error}</div> : null}
        {data?.message ? (
          <div className="text-xs text-amber-400 sm:col-span-3">{data.message}</div>
        ) : null}
      </section>

      {summary ? (
        <section className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-4">
            <Metric label="Precision" value={formatPercent(summary.precision)} hint="TP / (TP + FP)" />
            <Metric label="Recall" value={formatPercent(summary.recall)} hint="TP / (TP + FN + abstains on positives)" />
            <Metric label="F1" value={formatPercent(summary.f1)} hint="Harmonic mean of P and R" />
            <Metric label="Coverage" value={formatPercent(summary.coverage)} hint="Non-abstain predictions / validation set" />
          </div>
          <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-4">
            <Counter label="Validation set" value={summary.considered} sub={`${summary.total_gold} gold rows total`} />
            <Counter
              label="False negatives"
              value={summary.false_negative + summary.abstain_on_positive}
              sub={`${summary.false_negative} wrong direction · ${summary.abstain_on_positive} abstained`}
              accent="red"
            />
            <Counter label="False positives" value={summary.false_positive} accent="amber" />
            <Counter
              label="Correct"
              value={summary.true_positive + summary.true_negative}
              sub={`${summary.true_positive} TP · ${summary.true_negative} TN`}
              accent="emerald"
            />
          </div>
        </section>
      ) : null}

      {summary && summary.considered === 0 ? (
        <div className="rounded-md border border-ink-800 bg-ink-900/40 p-4 text-sm text-ink-200">
          No gold-labeled documents for this tag yet (with value ≠ 0). Open{" "}
          <a className="text-accent-400 hover:text-accent-300" href="/explore">
            Explore
          </a>
          , pick this tag, and vote +1 / −1 on a handful of documents to seed the validation set.
        </div>
      ) : null}

      {summary && summary.considered > 0 ? (
        <section className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-ink-500">Show categories:</span>
            {CATEGORY_ORDER.map((cat) => {
              const rowCount = rowsByCategory.get(cat)?.length ?? 0;
              const active = visibleCategories.has(cat);
              return (
                <button
                  key={cat}
                  type="button"
                  onClick={() => toggleCategory(cat)}
                  className={`rounded-full border px-2 py-1 transition-colors ${
                    active
                      ? "border-accent-500 bg-accent-600/20 text-white"
                      : "border-ink-700 bg-ink-950 text-ink-500 hover:border-ink-500"
                  }`}
                >
                  {CATEGORY_LABELS[cat]} ({rowCount})
                </button>
              );
            })}
          </div>

          {data?.truncated ? (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-200">
              The list of rows was truncated. Summary metrics above are exact; only the per-document
              listing is capped.
            </div>
          ) : null}

          <div className="space-y-6">
            {CATEGORY_ORDER.filter((c) => visibleCategories.has(c)).map((cat) => {
              const rows = rowsByCategory.get(cat) ?? [];
              if (!rows.length) return null;
              return (
                <div key={cat} className="space-y-2">
                  <h2 className="text-sm font-semibold text-white">
                    {CATEGORY_LABELS[cat]}{" "}
                    <span className="text-ink-500">· {rows.length}</span>
                  </h2>
                  <div className="space-y-2">
                    {rows.map((r) => (
                      <ErrorRow
                        key={r.document_id}
                        row={r}
                        expanded={expandedDocs.has(r.document_id)}
                        onToggle={() => toggleDoc(r.document_id)}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-md border border-ink-800 bg-ink-900/30 p-3">
      <div className="text-xs uppercase tracking-wide text-ink-500">{label}</div>
      <div className="mt-1 font-mono text-2xl text-white">{value}</div>
      <div className="mt-1 text-[11px] text-ink-500">{hint}</div>
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
  accent?: "red" | "amber" | "emerald";
}) {
  const color =
    accent === "red"
      ? "text-red-400"
      : accent === "amber"
        ? "text-amber-400"
        : accent === "emerald"
          ? "text-emerald-400"
          : "text-white";
  return (
    <div className="rounded-md border border-ink-800 bg-ink-900/30 p-3">
      <div className="text-xs uppercase tracking-wide text-ink-500">{label}</div>
      <div className={`mt-1 font-mono text-2xl ${color}`}>{value}</div>
      {sub ? <div className="mt-1 text-[11px] text-ink-500">{sub}</div> : null}
    </div>
  );
}

function ErrorRow({
  row,
  expanded,
  onToggle,
}: {
  row: EvaluationRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const color = CATEGORY_COLOR[row.category];
  return (
    <div className={`rounded-md border ${color} p-3`}>
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="font-mono text-ink-500">{row.document_id.slice(0, 8)}…</span>
        <Badge label="gold" value={formatVote(row.gold)} tone={row.gold === 1 ? "emerald" : row.gold === -1 ? "red" : "neutral"} />
        <Badge
          label="pred"
          value={formatVote(row.predicted)}
          tone={row.predicted === 1 ? "emerald" : row.predicted === -1 ? "red" : "neutral"}
        />
        <span className="text-ink-500">
          vote sum <span className="font-mono text-ink-200">{row.vote_sum}</span>
        </span>
        <button
          type="button"
          onClick={onToggle}
          className="ml-auto rounded border border-ink-700 bg-ink-950 px-2 py-1 text-[11px] text-ink-200 hover:border-accent-500"
        >
          {expanded ? "Hide details" : `Per-LF votes (${row.votes.length})`}
        </button>
      </div>
      <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-xs text-ink-200">
        {row.text_preview}
      </pre>
      {expanded ? (
        <div className="mt-3 space-y-1 border-t border-ink-800 pt-2 text-xs">
          {row.votes.length === 0 ? (
            <div className="text-ink-500">
              No LFs voted on this document — every LF in the run abstained or returned 0.
            </div>
          ) : (
            <table className="w-full text-left">
              <thead className="text-[11px] uppercase text-ink-500">
                <tr>
                  <th className="py-1">Labeling function</th>
                  <th className="py-1">Vote</th>
                </tr>
              </thead>
              <tbody>
                {row.votes.map((v) => (
                  <tr key={v.labeling_function_id} className="border-t border-ink-900">
                    <td className="py-1 text-ink-200">{v.labeling_function_name}</td>
                    <td className="py-1 font-mono text-ink-200">{formatVote(v.vote)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
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
