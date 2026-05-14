"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { components } from "@hinter/contracts";

import { api } from "@/lib/api";
import { describeMlFetchError } from "@/lib/ml-fetch-error";
import { useProject } from "@/lib/project-context";
import { NoProjectGate } from "@/components/NoProjectGate";

type Tag = components["schemas"]["Tag"];
type TopicModel = components["schemas"]["TopicModel"];
type Topic = components["schemas"]["Topic"];
type TopicWord = components["schemas"]["TopicWord"];
type TopicSuggestionsResponse = components["schemas"]["TopicSuggestionsResponse"];
type TopicSuggestion = components["schemas"]["TopicSuggestion"];
type RelevantTopic = components["schemas"]["RelevantTopic"];

const POLL_INTERVAL_MS = 2500;

// ─── helpers ─────────────────────────────────────────────────────────────────

function statusBadge(status: string) {
  const map: Record<string, string> = {
    pending: "bg-yellow-900/40 text-yellow-300",
    running: "bg-blue-900/40 text-blue-300",
    completed: "bg-emerald-900/40 text-emerald-300",
    failed: "bg-rose-900/40 text-rose-300",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${map[status] ?? "bg-ink-900 text-ink-400"}`}>
      {status}
    </span>
  );
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

// ─── page ─────────────────────────────────────────────────────────────────────

export default function TopicsPage() {
  const { projectId, hasActiveProject } = useProject();

  // Topic model runs list
  const [runs, setRuns] = useState<TopicModel[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<TopicModel | null>(null);

  // New run form
  const [nTopics, setNTopics] = useState(10);
  const [algorithm, setAlgorithm] = useState<"lda" | "nmf">("lda");
  const [maxFeatures, setMaxFeatures] = useState(5000);
  const [starting, setStarting] = useState(false);

  // Suggestions
  const [tags, setTags] = useState<Tag[]>([]);
  const [selectedTagId, setSelectedTagId] = useState("");
  const [suggestions, setSuggestions] = useState<TopicSuggestionsResponse | null>(null);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState<Set<string>>(new Set());

  // UI state
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── data loaders ────────────────────────────────────────────────────────────

  const loadRuns = useCallback(async () => {
    if (!projectId) { setRuns([]); return; }
    try {
      const { data } = await api.GET("/v1/topic-models", {});
      if (data) setRuns(data);
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  }, [projectId]);

  const loadSelectedRun = useCallback(async (id: string) => {
    try {
      const { data } = await api.GET("/v1/topic-models/{model_id}", { params: { path: { model_id: id } } });
      if (data) {
        setSelectedRun(data);
        // Update status in the list too
        setRuns((prev) => prev.map((r) => (r.id === id ? { ...r, status: data.status } : r)));
      }
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  }, []);

  const loadTags = useCallback(async () => {
    if (!projectId) { setTags([]); return; }
    try {
      const { data } = await api.GET("/v1/tags", {});
      if (data) setTags(data);
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  }, [projectId]);

  const loadSuggestions = useCallback(async (excludeList: string[] = []) => {
    if (!selectedRunId || !selectedTagId) return;
    setSuggestionsLoading(true);
    try {
      const { data } = await api.GET("/v1/topic-models/{model_id}/suggestions", {
        params: {
          path: { model_id: selectedRunId },
          query: { tag_id: selectedTagId, limit: 15, exclude: excludeList },
        },
      });
      if (data) setSuggestions(data);
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setSuggestionsLoading(false);
    }
  }, [selectedRunId, selectedTagId]);

  // ── effects ─────────────────────────────────────────────────────────────────

  useEffect(() => {
    void loadRuns();
    void loadTags();
  }, [loadRuns, loadTags]);

  // Poll active run until terminal state
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (!selectedRunId) return;

    const poll = async () => {
      const run = runs.find((r) => r.id === selectedRunId);
      if (!run || run.status === "completed" || run.status === "failed") {
        if (pollRef.current) clearInterval(pollRef.current);
        return;
      }
      await loadSelectedRun(selectedRunId);
    };

    pollRef.current = setInterval(() => { void poll(); }, POLL_INTERVAL_MS);
    void poll(); // immediate first fetch

    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId]);

  // Reload suggestions when tag changes or run completes
  useEffect(() => {
    setSuggestions(null);
    setExcluded(new Set());
    if (selectedRun?.status === "completed" && selectedTagId) {
      void loadSuggestions([]);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTagId, selectedRun?.status]);

  // ── actions ─────────────────────────────────────────────────────────────────

  const startRun = async () => {
    setError(null);
    setMessage(null);
    setStarting(true);
    try {
      const { data, error: err } = await api.POST("/v1/topic-models", {
        body: { n_topics: nTopics, algorithm, max_features: maxFeatures },
      });
      if (err || !data) { setError("Could not start topic model run"); return; }
      setMessage(`Topic model started (${data.id.slice(0, 8)}…)`);
      setRuns((prev) => [data, ...prev]);
      setSelectedRunId(data.id);
      setSelectedRun(data);
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setStarting(false);
    }
  };

  const selectRun = async (id: string) => {
    setSelectedRunId(id);
    setSuggestions(null);
    setExcluded(new Set());
    await loadSelectedRun(id);
  };

  const deleteRun = async (id: string) => {
    try {
      await api.DELETE("/v1/topic-models/{model_id}", { params: { path: { model_id: id } } });
      setRuns((prev) => prev.filter((r) => r.id !== id));
      if (selectedRunId === id) {
        setSelectedRunId(null);
        setSelectedRun(null);
        setSuggestions(null);
      }
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  };

  const addAsLf = async (word: string) => {
    if (!selectedTagId) { setError("Select a tag first"); return; }
    setAdding((prev) => new Set(prev).add(word));
    setError(null);
    try {
      const { data, error: err } = await api.POST("/v1/labeling-functions", {
        body: {
          tag_id: selectedTagId,
          name: `topic: ${word}`,
          type: "keywords",
          config: { keywords: [word], mode: "any", return_value: 1 },
          enabled: true,
        },
      });
      if (err || !data) { setError("Could not create labeling function"); return; }
      setMessage(`Created LF "${data.name}"`);
      // Exclude the word from future suggestions
      const next = new Set(excluded).add(word);
      setExcluded(next);
      setSuggestions((prev) =>
        prev ? { ...prev, suggestions: prev.suggestions.filter((s) => s.word !== word) } : prev,
      );
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setAdding((prev) => { const next = new Set(prev); next.delete(word); return next; });
    }
  };

  const dismissSuggestion = (word: string) => {
    const next = new Set(excluded).add(word);
    setExcluded(next);
    setSuggestions((prev) =>
      prev ? { ...prev, suggestions: prev.suggestions.filter((s) => s.word !== word) } : prev,
    );
    void loadSuggestions(Array.from(next));
  };

  // ── render ───────────────────────────────────────────────────────────────────

  if (!hasActiveProject) {
    return (
      <div className="space-y-4">
        <div>
          <h1 className="text-xl font-semibold text-white">Topic Modeling</h1>
          <p className="mt-1 text-sm text-ink-400">
            Discover latent topics in your corpus and use them to generate keyword hinter suggestions.
          </p>
        </div>
        <NoProjectGate pageName="Topic Modeling" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-white">Topic Modeling</h1>
        <p className="mt-1 text-sm text-ink-400">
          Discover latent topics in your corpus and use them to generate keyword hinter suggestions.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
          {/* ── Left column: run form + run list ── */}
          <aside className="space-y-5">
            {/* Run form */}
            <section className="space-y-4 rounded-lg border border-ink-900 bg-ink-900/30 p-4">
              <h2 className="text-sm font-semibold text-white">New topic model run</h2>

              <label className="block text-xs text-ink-500">
                Number of topics
                <div className="mt-1 flex items-center gap-3">
                  <input
                    type="range"
                    min={2}
                    max={50}
                    value={nTopics}
                    onChange={(e) => setNTopics(Number(e.target.value))}
                    className="flex-1 accent-accent-500"
                  />
                  <span className="w-6 text-right text-sm text-white">{nTopics}</span>
                </div>
              </label>

              <label className="block text-xs text-ink-500">
                Algorithm
                <select
                  className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 text-sm text-white"
                  value={algorithm}
                  onChange={(e) => setAlgorithm(e.target.value as "lda" | "nmf")}
                >
                  <option value="lda">LDA (Latent Dirichlet Allocation)</option>
                  <option value="nmf">NMF (Non-negative Matrix Factorization)</option>
                </select>
              </label>

              <label className="block text-xs text-ink-500">
                Max vocabulary size
                <input
                  type="number"
                  min={100}
                  max={50000}
                  step={500}
                  value={maxFeatures}
                  onChange={(e) => setMaxFeatures(Number(e.target.value))}
                  className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 text-sm text-white"
                />
              </label>

              <button
                type="button"
                disabled={starting}
                onClick={() => void startRun()}
                className="w-full rounded-md bg-accent-600 px-3 py-2 text-xs font-medium text-white hover:bg-accent-500 disabled:opacity-50"
              >
                {starting ? "Starting…" : "Run topic model"}
              </button>

              {message && <p className="text-xs text-emerald-400">{message}</p>}
              {error && <p className="text-xs text-rose-400">{error}</p>}
            </section>

            {/* Run list */}
            <section className="space-y-2 rounded-lg border border-ink-900 bg-ink-900/30 p-4">
              <h2 className="text-sm font-semibold text-white">Past runs</h2>
              {runs.length === 0 ? (
                <p className="text-xs text-ink-500">No runs yet. Start one above.</p>
              ) : (
                <ul className="space-y-2">
                  {runs.map((run) => (
                    <li key={run.id}>
                      <button
                        type="button"
                        onClick={() => void selectRun(run.id)}
                        className={`w-full rounded-md border px-3 py-2 text-left text-xs transition-colors ${
                          selectedRunId === run.id
                            ? "border-accent-600 bg-accent-900/20 text-white"
                            : "border-ink-800 bg-ink-950/40 text-ink-300 hover:border-ink-600"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-[10px] text-ink-500">{run.id.slice(0, 8)}…</span>
                          {statusBadge(run.status)}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-2">
                          <span className="text-white">{run.n_topics} topics</span>
                          <span className="text-ink-500 uppercase">{run.algorithm}</span>
                          <span className="text-ink-500">{fmtDate(run.created_at)}</span>
                        </div>
                        {run.documents_processed > 0 && (
                          <div className="mt-0.5 text-[10px] text-ink-500">
                            {run.documents_processed} docs processed
                          </div>
                        )}
                        {run.error && (
                          <div className="mt-1 truncate text-[10px] text-rose-400">{run.error}</div>
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => void deleteRun(run.id)}
                        className="mt-1 text-[10px] text-ink-600 hover:text-rose-400"
                      >
                        Delete
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </aside>

          {/* ── Right column: topics + suggestions ── */}
          <div className="space-y-6 min-w-0">
            {!selectedRun ? (
              <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-ink-800 text-sm text-ink-500">
                Select a run on the left to inspect its topics.
              </div>
            ) : selectedRun.status === "pending" || selectedRun.status === "running" ? (
              <div className="flex h-40 items-center justify-center gap-3 rounded-lg border border-ink-800 bg-ink-900/20 text-sm text-ink-400">
                <span className="animate-spin text-lg">⏳</span>
                <span>
                  {selectedRun.status === "pending" ? "Waiting to start…" : "Fitting topic model…"}
                </span>
              </div>
            ) : selectedRun.status === "failed" ? (
              <div className="rounded-lg border border-rose-800 bg-rose-900/10 p-4 text-sm text-rose-300">
                <div className="font-semibold">Run failed</div>
                <div className="mt-1 text-xs">{selectedRun.error ?? "Unknown error"}</div>
              </div>
            ) : (
              <>
                {/* Topics grid */}
                <section className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-white">
                      Discovered topics
                      <span className="ml-2 text-xs font-normal text-ink-500">
                        {selectedRun.topics?.length ?? 0} topics · {selectedRun.documents_processed} docs · {selectedRun.algorithm.toUpperCase()}
                      </span>
                    </h2>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {(selectedRun.topics ?? []).map((topic) => (
                      <TopicCard
                        key={topic.id}
                        topic={topic}
                        relevantTopicIds={
                          new Set(suggestions?.relevant_topics.map((rt) => rt.topic_id) ?? [])
                        }
                      />
                    ))}
                  </div>
                </section>

                {/* Suggestions */}
                <SuggestionsPanel
                  tags={tags}
                  selectedTagId={selectedTagId}
                  onSelectTag={(id) => setSelectedTagId(id)}
                  suggestions={suggestions}
                  loading={suggestionsLoading}
                  onRefresh={() => void loadSuggestions(Array.from(excluded))}
                  onAdd={addAsLf}
                  onDismiss={dismissSuggestion}
                  adding={adding}
                />
              </>
            )}
          </div>
        </div>
    </div>
  );
}

// ─── sub-components ───────────────────────────────────────────────────────────

function TopicCard({
  topic,
  relevantTopicIds,
}: {
  topic: Topic;
  relevantTopicIds: Set<number>;
}) {
  const isRelevant = relevantTopicIds.has(topic.id);
  const maxW = Math.max(...topic.top_words.map((w) => w.weight), 1e-9);

  return (
    <div
      className={`rounded-lg border p-3 text-xs transition-colors ${
        isRelevant
          ? "border-accent-600 bg-accent-900/15"
          : "border-ink-800 bg-ink-950/40"
      }`}
    >
      <div className="mb-2 flex items-center justify-between gap-1">
        <span className="font-medium text-ink-400">Topic {topic.id}</span>
        {isRelevant && (
          <span className="rounded bg-accent-900/50 px-1.5 py-0.5 text-[10px] text-accent-300">
            relevant
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1">
        {topic.top_words.slice(0, 10).map((tw) => (
          <WordChip key={tw.word} word={tw} maxWeight={maxW} />
        ))}
      </div>
    </div>
  );
}

function WordChip({ word, maxWeight }: { word: TopicWord; maxWeight: number }) {
  const opacity = 0.4 + 0.6 * (word.weight / maxWeight);
  return (
    <span
      className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-[11px] text-white"
      style={{ opacity }}
      title={`weight: ${word.weight.toFixed(4)}`}
    >
      {word.word}
    </span>
  );
}

function SuggestionsPanel({
  tags,
  selectedTagId,
  onSelectTag,
  suggestions,
  loading,
  onRefresh,
  onAdd,
  onDismiss,
  adding,
}: {
  tags: Tag[];
  selectedTagId: string;
  onSelectTag: (id: string) => void;
  suggestions: TopicSuggestionsResponse | null;
  loading: boolean;
  onRefresh: () => void;
  onAdd: (word: string) => Promise<void>;
  onDismiss: (word: string) => void;
  adding: Set<string>;
}) {
  return (
    <section className="space-y-4 rounded-lg border border-ink-900 bg-ink-900/30 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-white">Hinter suggestions from topics</h2>
          <p className="mt-0.5 text-xs text-ink-500">
            {suggestions?.basis === "gold"
              ? "Topics aligned to your gold labels — keywords ranked by topic relevance."
              : suggestions?.basis === "corpus"
              ? "No gold labels yet — showing top corpus-wide topic keywords."
              : "Pick a tag below to generate keyword suggestions."}
          </p>
        </div>
        <button
          type="button"
          disabled={!selectedTagId || loading}
          onClick={onRefresh}
          className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-ink-200 hover:border-accent-500 disabled:opacity-40"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {/* Tag selector */}
      <label className="block text-xs text-ink-500">
        Tag
        <select
          className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 text-sm text-white"
          value={selectedTagId}
          onChange={(e) => onSelectTag(e.target.value)}
        >
          <option value="">— select a tag —</option>
          {tags.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>

      {/* Relevant topics legend */}
      {suggestions && suggestions.relevant_topics.length > 0 && (
        <div className="space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-500">
            Most relevant topics
          </div>
          <div className="flex flex-wrap gap-2">
            {suggestions.relevant_topics.map((rt) => (
              <span
                key={rt.topic_id}
                className="rounded border border-accent-800 bg-accent-900/20 px-2 py-1 text-xs text-accent-300"
                title={`relevance: ${rt.relevance_score.toFixed(3)}`}
              >
                Topic {rt.topic_id}
                <span className="ml-1 text-[10px] text-ink-500">
                  ({(rt.relevance_score * 100).toFixed(1)}%)
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Keyword suggestion list */}
      {suggestions && suggestions.suggestions.length > 0 ? (
        <div className="space-y-2">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-400">
            Keyword candidates
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {suggestions.suggestions.map((s) => (
              <SuggestionRow
                key={s.word}
                suggestion={s}
                isAdding={adding.has(s.word)}
                onAdd={() => void onAdd(s.word)}
                onDismiss={() => onDismiss(s.word)}
              />
            ))}
          </div>
        </div>
      ) : suggestions && !loading ? (
        <p className="text-xs text-ink-500">
          {selectedTagId ? "No keyword candidates found for this tag." : "Select a tag above."}
        </p>
      ) : null}
    </section>
  );
}

function SuggestionRow({
  suggestion,
  isAdding,
  onAdd,
  onDismiss,
}: {
  suggestion: TopicSuggestion;
  isAdding: boolean;
  onAdd: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-ink-900 bg-ink-950/40 px-3 py-2 text-xs">
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono text-sm text-white truncate">{suggestion.word}</span>
        <span
          className="rounded bg-ink-900 px-1.5 py-0.5 text-[10px] text-ink-500"
          title="Combined relevance score"
        >
          {suggestion.score.toFixed(3)}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          disabled={isAdding}
          onClick={onAdd}
          className="rounded bg-emerald-700 px-2 py-1 text-[10px] font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          {isAdding ? "Adding…" : "Add as +1 LF"}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="rounded border border-ink-700 px-2 py-1 text-[10px] text-ink-400 hover:border-ink-500 hover:text-white"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
