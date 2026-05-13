"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { components } from "@hinter/contracts";

import { api } from "@/lib/api";
import { describeMlFetchError } from "@/lib/ml-fetch-error";
import { mlFetch } from "@/lib/ml-fetch";
import { useProject } from "@/lib/project-context";
import { NoProjectGate } from "@/components/NoProjectGate";

type Tag = components["schemas"]["Tag"];
type LabelingFunction = components["schemas"]["LabelingFunction"];
type LabelingFunctionType = components["schemas"]["LabelingFunctionType"];
type LfRun = components["schemas"]["LfRun"];
type SparseLabelMatrix = components["schemas"]["SparseLabelMatrix"];
type HinterSuggestion = components["schemas"]["HinterSuggestion"];
type HinterSuggestionsBasis = components["schemas"]["HinterSuggestionsBasis"];

const LF_TYPES: LabelingFunctionType[] = ["regex", "keywords", "structural", "zeroshot", "llm_prompt"];

const DEFAULT_CONFIG: Record<LabelingFunctionType, string> = {
  regex: '{\n  "pattern": "\\\\bsecurity\\\\b",\n  "flags": "i"\n}',
  keywords: '{\n  "keywords": ["warranty", "liability"],\n  "mode": "any"\n}',
  structural:
    '{\n  "length_gte": 120,\n  "caps_ratio_lte": 0.35,\n  "punctuation_ratio_gte": 0.02\n}',
  zeroshot: "{}",
  llm_prompt: "{}",
};

export default function StudioPage() {
  const { projectId, hasActiveProject } = useProject();
  const [tags, setTags] = useState<Tag[]>([]);
  const [tagName, setTagName] = useState("default");
  const [selectedTagId, setSelectedTagId] = useState<string>("");
  const [lfs, setLfs] = useState<LabelingFunction[]>([]);
  const [lfName, setLfName] = useState("My LF");
  const [lfType, setLfType] = useState<LabelingFunctionType>("regex");
  const [lfConfig, setLfConfig] = useState(DEFAULT_CONFIG.regex);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string>("");
  const [selectedLfIds, setSelectedLfIds] = useState<string[]>([]);
  const [lastRun, setLastRun] = useState<LfRun | null>(null);
  const [matrix, setMatrix] = useState<SparseLabelMatrix | null>(null);
  const [suggestions, setSuggestions] = useState<HinterSuggestion[]>([]);
  const [suggestionsBasis, setSuggestionsBasis] = useState<HinterSuggestionsBasis | null>(null);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState<Set<string>>(new Set());
  const [editingLfId, setEditingLfId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editConfig, setEditConfig] = useState("");
  const [editEnabled, setEditEnabled] = useState(true);
  const [savingEdit, setSavingEdit] = useState(false);
  const [deletingLfId, setDeletingLfId] = useState<string | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const [runTotalDocs, setRunTotalDocs] = useState(0);

  useEffect(() => {
    setLfConfig(DEFAULT_CONFIG[lfType]);
  }, [lfType]);

  const loadTags = useCallback(async () => {
    if (!projectId) {
      setTags([]);
      return;
    }
    try {
      const { data } = await api.GET("/v1/tags", {});
      if (data) {
        setTags(data);
        setError(null);
      }
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  }, [projectId]);

  const loadLfs = useCallback(async () => {
    if (!selectedTagId) return;
    try {
      const { data } = await api.GET("/v1/labeling-functions", { params: { query: { tag_id: selectedTagId } } });
      if (data) {
        setLfs(data);
        setSelectedLfIds(data.filter((x) => x.enabled).map((x) => x.id));
        setError(null);
      }
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  }, [selectedTagId]);

  const loadSuggestions = useCallback(
    async (excludeList: string[] = []) => {
      if (!selectedTagId) {
        setSuggestions([]);
        setSuggestionsBasis(null);
        return;
      }
      setSuggestionsLoading(true);
      try {
        const { data } = await api.GET("/v1/labeling-functions/suggestions", {
          params: {
            query: {
              tag_id: selectedTagId,
              limit: 10,
              ...(excludeList.length ? { exclude: excludeList } : {}),
            },
          },
        });
        if (data) {
          setSuggestions(data.suggestions);
          setSuggestionsBasis(data.basis);
        }
      } catch (e) {
        setError(describeMlFetchError(e));
      } finally {
        setSuggestionsLoading(false);
      }
    },
    [selectedTagId],
  );

  useEffect(() => {
    void loadTags();
  }, [loadTags, projectId]);

  useEffect(() => {
    setSelectedTagId("");
    setLfs([]);
    setSelectedLfIds([]);
    setLastRun(null);
    setMatrix(null);
    setSuggestions([]);
    setSuggestionsBasis(null);
    setDismissed(new Set());
    setEditingLfId(null);
  }, [projectId]);

  useEffect(() => {
    setSuggestions([]);
    setSuggestionsBasis(null);
    setDismissed(new Set());
    setEditingLfId(null);
  }, [selectedTagId]);

  useEffect(() => {
    if (!selectedTagId && tags[0]) setSelectedTagId(tags[0].id);
  }, [tags, selectedTagId]);

  useEffect(() => {
    void loadLfs();
  }, [loadLfs]);

  useEffect(() => {
    void loadSuggestions();
  }, [loadSuggestions]);

  const selectedTag = useMemo(() => tags.find((t) => t.id === selectedTagId), [tags, selectedTagId]);

  const visiblePositive = useMemo(
    () => suggestions.filter((s) => s.return_value === 1 && !dismissed.has(s.keyword)),
    [suggestions, dismissed],
  );
  const visibleNegative = useMemo(
    () => suggestions.filter((s) => s.return_value === -1 && !dismissed.has(s.keyword)),
    [suggestions, dismissed],
  );

  const basisLabel = useMemo(() => {
    if (!suggestionsBasis) return null;
    if (suggestionsBasis === "gold") return "Mined from gold labels for this tag.";
    if (suggestionsBasis === "mixed") return "Mined from gold labels for this tag, plus the tag name.";
    return "Cold-start suggestions from the tag name (label a few documents to improve these).";
  }, [suggestionsBasis]);

  const createTag = async () => {
    setError(null);
    try {
      const { data, error: err } = await api.POST("/v1/tags", { body: { name: tagName, taxonomy_version: "v1" } });
      if (err || !data) {
        setError("Could not create tag (name must be unique)");
        return;
      }
      setMessage(`Created tag ${data.name}`);
      setSelectedTagId(data.id);
      await loadTags();
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  };

  const createLf = async () => {
    setError(null);
    if (!selectedTagId) {
      setError("Select a tag first");
      return;
    }
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(lfConfig) as Record<string, unknown>;
    } catch {
      setError("Config must be valid JSON");
      return;
    }
    try {
      const { data, error: err } = await api.POST("/v1/labeling-functions", {
        body: { tag_id: selectedTagId, name: lfName, type: lfType, config, enabled: true },
      });
      if (err || !data) {
        setError("Could not create labeling function");
        return;
      }
      setMessage(`Created LF ${data.name}`);
      await loadLfs();
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  };

  const dismissSuggestion = (keyword: string) => {
    const nextDismissed = new Set(dismissed);
    nextDismissed.add(keyword);
    setDismissed(nextDismissed);
    setSuggestions((prev) => prev.filter((s) => s.keyword !== keyword));
    void loadSuggestions(Array.from(nextDismissed));
  };

  const addSuggestion = async (suggestion: HinterSuggestion) => {
    setError(null);
    if (!selectedTagId) {
      setError("Select a tag first");
      return;
    }
    const keyword = suggestion.keyword;
    setAdding((prev) => {
      const next = new Set(prev);
      next.add(keyword);
      return next;
    });
    const direction = suggestion.return_value === -1 ? "neg" : "pos";
    try {
      const { data, error: err } = await api.POST("/v1/labeling-functions", {
        body: {
          tag_id: selectedTagId,
          name: `suggested ${direction}: ${keyword}`,
          type: "keywords",
          config: { keywords: [keyword], mode: "any", return_value: suggestion.return_value },
          enabled: true,
        },
      });
      if (err || !data) {
        setError("Could not create labeling function from suggestion");
        return;
      }
      setMessage(`Created LF ${data.name}`);
      setSuggestions((prev) => prev.filter((s) => s.keyword !== keyword));
      await loadLfs();
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setAdding((prev) => {
        const next = new Set(prev);
        next.delete(keyword);
        return next;
      });
    }
  };

  const beginEdit = (lf: LabelingFunction) => {
    setError(null);
    setEditingLfId(lf.id);
    setEditName(lf.name);
    setEditConfig(JSON.stringify(lf.config ?? {}, null, 2));
    setEditEnabled(lf.enabled);
  };

  const cancelEdit = () => {
    setEditingLfId(null);
    setEditName("");
    setEditConfig("");
    setEditEnabled(true);
  };

  const saveEdit = async () => {
    if (!editingLfId) return;
    setError(null);
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(editConfig) as Record<string, unknown>;
    } catch {
      setError("Config must be valid JSON");
      return;
    }
    setSavingEdit(true);
    try {
      const { data, error: err } = await api.PATCH("/v1/labeling-functions/{labeling_function_id}", {
        params: { path: { labeling_function_id: editingLfId } },
        body: { name: editName, config, enabled: editEnabled },
      });
      if (err || !data) {
        setError("Could not update labeling function");
        return;
      }
      setMessage(`Updated LF ${data.name}`);
      cancelEdit();
      await loadLfs();
      // Coverage may have changed — refresh suggestions so newly-uncovered
      // tokens can re-appear and newly-covered ones disappear.
      void loadSuggestions();
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setSavingEdit(false);
    }
  };

  const deleteLf = async (lf: LabelingFunction) => {
    if (typeof window !== "undefined") {
      if (!window.confirm(`Delete labeling function "${lf.name}"? This cannot be undone.`)) {
        return;
      }
    }
    setError(null);
    setDeletingLfId(lf.id);
    try {
      const { error: err } = await api.DELETE("/v1/labeling-functions/{labeling_function_id}", {
        params: { path: { labeling_function_id: lf.id } },
      });
      if (err) {
        setError("Could not delete labeling function");
        return;
      }
      setMessage(`Deleted LF ${lf.name}`);
      if (editingLfId === lf.id) cancelEdit();
      setSelectedLfIds((prev) => prev.filter((x) => x !== lf.id));
      await loadLfs();
      void loadSuggestions();
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setDeletingLfId(null);
    }
  };

  const runPreview = async (lfId: string) => {
    setError(null);
    setPreview("");
    try {
      const { data, error: err } = await api.POST("/v1/labeling-functions/{labeling_function_id}/preview", {
        params: { path: { labeling_function_id: lfId } },
        body: { limit: 15 },
      });
      if (err || !data) {
        setError("Preview failed");
        return;
      }
      setPreview(JSON.stringify(data.rows, null, 2));
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  };

  const toggleLf = (id: string) => {
    setSelectedLfIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const runBatch = async () => {
    setError(null);
    setMatrix(null);
    setLastRun(null);
    if (!selectedTagId || selectedLfIds.length === 0) {
      setError("Pick at least one labeling function");
      return;
    }
    setBatchRunning(true);
    setRunTotalDocs(0);
    try {
      // Fetch total document count so the progress bar has a denominator.
      try {
        const countRes = await mlFetch("/api/ml/v1/documents?limit=1");
        if (countRes.ok) {
          const countData = (await countRes.json()) as { total: number };
          setRunTotalDocs(countData.total ?? 0);
        }
      } catch {
        /* non-fatal — progress bar will be indeterminate */
      }

      const res = await mlFetch("/api/ml/v1/lf-runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag_id: selectedTagId, labeling_function_ids: selectedLfIds }),
      });
      if (!res.ok) {
        let detail = `Run failed (HTTP ${res.status})`;
        try {
          const body = (await res.json()) as { detail?: string | { msg: string }[] };
          if (typeof body.detail === "string") {
            detail = body.detail;
          } else if (Array.isArray(body.detail) && body.detail.length > 0) {
            detail = body.detail.map((e) => e.msg).join("; ");
          }
        } catch {
          /* non-JSON error body — keep the default message */
        }
        setError(detail);
        return;
      }

      let run = (await res.json()) as LfRun;
      setLastRun(run);
      setMessage(`Running… (${run.documents_scanned} docs scanned)`);

      // The run executes in a background task on the server — poll until done.
      const POLL_INTERVAL_MS = 1500;
      const POLL_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes
      const deadline = Date.now() + POLL_TIMEOUT_MS;
      while (run.status === "running" && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        try {
          const poll = await mlFetch(`/api/ml/v1/lf-runs/${run.id}`);
          if (poll.ok) {
            run = (await poll.json()) as LfRun;
            setLastRun(run);
            setMessage(
              run.status === "running"
                ? `Running… (${run.documents_scanned} docs scanned, ${run.votes_written} votes)`
                : `Run ${run.id} — ${run.status}`,
            );
          }
        } catch {
          /* transient poll error — keep waiting */
        }
      }

      if (run.status === "running") {
        setError("Run timed out on the client side — the server may still be working. Refresh the page to check.");
        return;
      }
      if (run.status === "failed") {
        setError(run.error ?? "Run failed");
        return;
      }
      if (run.status === "completed") {
        const m = await mlFetch(`/api/ml/v1/lf-runs/${run.id}/matrix`);
        if (m.ok) {
          setMatrix((await m.json()) as SparseLabelMatrix);
        }
      }
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setBatchRunning(false);
    }
  };

  const loadMatrix = async () => {
    if (!lastRun?.id) return;
    try {
      const m = await mlFetch(`/api/ml/v1/lf-runs/${lastRun.id}/matrix`);
      if (m.ok) setMatrix((await m.json()) as SparseLabelMatrix);
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  };

  if (!hasActiveProject) {
    return (
      <div className="space-y-8">
        <div>
          <h1 className="text-2xl font-semibold text-white">LF Studio</h1>
          <p className="mt-2 max-w-3xl text-sm text-ink-500">
            Author regex, keyword, and structural labeling functions, preview votes, then run a
            batch job.
          </p>
        </div>
        <NoProjectGate pageName="LF Studio" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-white">LF Studio</h1>
        <p className="mt-2 max-w-3xl text-sm text-ink-500">
          Author regex, keyword, and structural labeling functions, preview votes on recent documents, then run a batch
          job and           export the sparse label matrix (<span className="text-ink-200">+1 / 0 / -1</span> as{" "}
          <span className="text-ink-200">positive / abstain / negative</span>).
        </p>
      </div>

      {(message || error) && (
        <div className="rounded-md border border-ink-900 bg-ink-900/40 px-3 py-2 text-sm">
          {message ? <div className="text-ink-200">{message}</div> : null}
          {error ? <div className="text-red-400">{error}</div> : null}
        </div>
      )}

      <section className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-4 rounded-lg border border-ink-900 bg-ink-900/30 p-4">
          <h2 className="text-sm font-semibold text-white">Tags</h2>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs text-ink-500">
              New tag name
              <input
                className="mt-1 block w-56 rounded-md border border-ink-700 bg-ink-950 px-2 py-1 text-sm text-white"
                value={tagName}
                onChange={(e) => setTagName(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="rounded-md bg-accent-600 px-3 py-2 text-xs font-medium text-white hover:bg-accent-500"
              onClick={() => void createTag()}
            >
              Create tag
            </button>
          </div>
          <label className="block text-xs text-ink-500">
            Active tag
            <select
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-2 text-sm text-white"
              value={selectedTagId}
              onChange={(e) => setSelectedTagId(e.target.value)}
            >
              {tags.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.taxonomy_version})
                </option>
              ))}
            </select>
          </label>
          {selectedTag ? (
            <p className="text-xs text-ink-500">
              Tag id: <span className="font-mono text-ink-200">{selectedTag.id}</span>
            </p>
          ) : (
            <p className="text-xs text-ink-500">Create a tag to begin authoring LFs.</p>
          )}
        </div>

        <div className="space-y-3 rounded-lg border border-ink-900 bg-ink-900/30 p-4">
          <h2 className="text-sm font-semibold text-white">New labeling function</h2>
          <label className="block text-xs text-ink-500">
            Name
            <input
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-1 text-sm text-white"
              value={lfName}
              onChange={(e) => setLfName(e.target.value)}
            />
          </label>
          <label className="block text-xs text-ink-500">
            Type
            <select
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-2 text-sm text-white"
              value={lfType}
              onChange={(e) => setLfType(e.target.value as LabelingFunctionType)}
            >
              {LF_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs text-ink-500">
            Config (JSON)
            <textarea
              className="mt-1 h-40 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-2 font-mono text-xs text-ink-200"
              value={lfConfig}
              onChange={(e) => setLfConfig(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="rounded-md bg-accent-600 px-3 py-2 text-xs font-medium text-white hover:bg-accent-500"
            onClick={() => void createLf()}
          >
            Save labeling function
          </button>
        </div>
      </section>

      {selectedTagId ? (
        <section className="space-y-3 rounded-lg border border-ink-900 bg-ink-900/30 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-white">Suggested hinters</h2>
              <p className="mt-1 text-xs text-ink-500">
                {basisLabel ?? "Keyword candidates inferred from this project. Click Add as LF to create a keywords labeling function in one step."}
              </p>
            </div>
            <button
              type="button"
              className="rounded-md border border-ink-700 px-3 py-2 text-xs text-ink-200 hover:border-accent-500"
              onClick={() => void loadSuggestions(Array.from(dismissed))}
              disabled={suggestionsLoading}
            >
              {suggestionsLoading ? "Loading..." : "Refresh suggestions"}
            </button>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {(
              [
                { label: "Positive hinters (+1)", list: visiblePositive, isPositive: true },
                { label: "Negative hinters (−1)", list: visibleNegative, isPositive: false },
              ] as const
            ).map(({ label, list, isPositive }) => (
              <div key={label} className="space-y-2">
                <div
                  className={`text-[11px] font-semibold uppercase tracking-wide ${isPositive ? "text-emerald-400" : "text-rose-400"}`}
                >
                  {label}
                </div>
                {list.map((s) => {
                  const isAdding = adding.has(s.keyword);
                  return (
                    <div
                      key={s.keyword}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-ink-900 bg-ink-950/40 px-3 py-2 text-xs"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm text-white">{s.keyword}</span>
                        <span
                          className="rounded bg-emerald-900/40 px-2 py-0.5 text-[10px] font-medium text-emerald-300"
                          title="Distinct gold-positive documents containing this token"
                        >
                          +{s.positive_hits}
                        </span>
                        <span
                          className="rounded bg-rose-900/40 px-2 py-0.5 text-[10px] font-medium text-rose-300"
                          title="Distinct gold-negative documents containing this token"
                        >
                          −{s.negative_hits}
                        </span>
                        <span
                          className="rounded bg-ink-900 px-2 py-0.5 text-[10px] uppercase tracking-wide text-ink-500"
                          title="Heuristic ranking score; higher is more confident"
                        >
                          score {s.score.toFixed(2)}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          className={`rounded-md px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-50 ${isPositive ? "bg-accent-600 hover:bg-accent-500" : "bg-rose-700 hover:bg-rose-600"}`}
                          onClick={() => void addSuggestion(s)}
                          disabled={isAdding}
                        >
                          {isAdding ? "Adding..." : "Add as LF"}
                        </button>
                        <button
                          type="button"
                          className="rounded-md border border-ink-700 px-2 py-1 text-[11px] text-ink-200 hover:border-accent-500"
                          onClick={() => dismissSuggestion(s.keyword)}
                          disabled={isAdding}
                        >
                          Dismiss
                        </button>
                      </div>
                    </div>
                  );
                })}
                {!list.length && !suggestionsLoading ? (
                  <div className="text-xs text-ink-500">
                    {dismissed.size
                      ? "No more candidates (some dismissed). Add gold labels to surface new ones."
                      : isPositive
                        ? "No positive candidates — label some +1 documents to seed these."
                        : "No negative candidates — label some −1 documents to seed these."}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="space-y-3 rounded-lg border border-ink-900 bg-ink-900/30 p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-white">Batch run</h2>
          <button
            type="button"
            className="rounded-md border border-ink-700 px-3 py-2 text-xs text-ink-200 hover:border-accent-500"
            onClick={() => void loadLfs()}
          >
            Refresh list
          </button>
        </div>
        <p className="text-xs text-ink-500">
          Select LFs to include, then execute over the full corpus. Runs are synchronous on the API for the MVP.
        </p>
        <div className="space-y-2">
          {lfs.map((lf) => {
            const isEditing = editingLfId === lf.id;
            const isDeleting = deletingLfId === lf.id;
            if (isEditing) {
              return (
                <div
                  key={lf.id}
                  className="space-y-3 rounded-md border border-accent-600/60 bg-ink-950/60 px-3 py-3 text-xs"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[11px] uppercase tracking-wide text-ink-500">
                      Editing labeling function
                    </span>
                    <span className="rounded bg-ink-900 px-2 py-0.5 text-[10px] uppercase tracking-wide text-ink-500">
                      {lf.type}
                    </span>
                  </div>
                  <label className="block text-xs text-ink-500">
                    Name
                    <input
                      className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-1 text-sm text-white"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                    />
                  </label>
                  <label className="block text-xs text-ink-500">
                    Config (JSON)
                    <textarea
                      className="mt-1 h-40 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-2 font-mono text-xs text-ink-200"
                      value={editConfig}
                      onChange={(e) => setEditConfig(e.target.value)}
                    />
                  </label>
                  <label className="flex items-center gap-2 text-ink-200">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-ink-700 bg-ink-950"
                      checked={editEnabled}
                      onChange={(e) => setEditEnabled(e.target.checked)}
                    />
                    Enabled (included by default in new runs)
                  </label>
                  <p className="text-[11px] text-ink-500">
                    To change the type, delete this LF and create a new one.
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      className="rounded-md bg-accent-600 px-3 py-1.5 text-[11px] font-medium text-white hover:bg-accent-500 disabled:opacity-50"
                      onClick={() => void saveEdit()}
                      disabled={savingEdit}
                    >
                      {savingEdit ? "Saving..." : "Save changes"}
                    </button>
                    <button
                      type="button"
                      className="rounded-md border border-ink-700 px-3 py-1.5 text-[11px] text-ink-200 hover:border-accent-500"
                      onClick={cancelEdit}
                      disabled={savingEdit}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              );
            }
            return (
              <div
                key={lf.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-ink-900 bg-ink-950/40 px-3 py-2 text-xs"
              >
                <label className="flex items-center gap-2 text-ink-200">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-ink-700 bg-ink-950"
                    checked={selectedLfIds.includes(lf.id)}
                    onChange={() => toggleLf(lf.id)}
                  />
                  <span className="font-medium text-white">{lf.name}</span>
                  <span className="rounded bg-ink-900 px-2 py-0.5 text-[10px] uppercase tracking-wide text-ink-500">
                    {lf.type}
                  </span>
                  {!lf.enabled ? (
                    <span className="rounded bg-amber-900/40 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                      disabled
                    </span>
                  ) : null}
                </label>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="rounded-md border border-ink-700 px-2 py-1 text-[11px] text-ink-200 hover:border-accent-500"
                    onClick={() => void runPreview(lf.id)}
                  >
                    Preview
                  </button>
                  <button
                    type="button"
                    className="rounded-md border border-ink-700 px-2 py-1 text-[11px] text-ink-200 hover:border-accent-500"
                    onClick={() => beginEdit(lf)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="rounded-md border border-rose-900/60 px-2 py-1 text-[11px] text-rose-300 hover:border-rose-500 disabled:opacity-50"
                    onClick={() => void deleteLf(lf)}
                    disabled={isDeleting}
                  >
                    {isDeleting ? "Deleting..." : "Delete"}
                  </button>
                </div>
              </div>
            );
          })}
          {!lfs.length ? <div className="text-xs text-ink-500">No labeling functions for this tag yet.</div> : null}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="rounded-md bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-500 disabled:opacity-50"
            onClick={() => void runBatch()}
            disabled={batchRunning}
          >
            Run batch + export matrix
          </button>
          {lastRun?.status === "completed" ? (
            <button
              type="button"
              className="rounded-md border border-ink-700 px-3 py-2 text-xs text-ink-200 hover:border-accent-500"
              onClick={() => void loadMatrix()}
            >
              Reload matrix JSON
            </button>
          ) : null}
          {batchRunning ? (
            <RunProgress scanned={lastRun?.documents_scanned ?? 0} total={runTotalDocs} votes={lastRun?.votes_written ?? 0} />
          ) : lastRun ? (
            <span className={`text-xs ${lastRun.status === "completed" ? "text-emerald-400" : lastRun.status === "failed" ? "text-red-400" : "text-ink-400"}`}>
              {lastRun.status === "completed"
                ? `Completed — ${lastRun.documents_scanned} docs, ${lastRun.votes_written} votes`
                : lastRun.status === "failed"
                  ? `Failed: ${lastRun.error ?? "unknown error"}`
                  : lastRun.status}
            </span>
          ) : null}
        </div>
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">Preview output</h3>
          <pre className="mt-2 max-h-80 overflow-auto rounded-md border border-ink-900 bg-black/40 p-3 text-[11px] text-ink-200">
            {preview || "// Click Preview on a labeling function"}
          </pre>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">Sparse matrix export</h3>

          <pre className="mt-2 max-h-80 overflow-auto rounded-md border border-ink-900 bg-black/40 p-3 text-[11px] text-ink-200">
            {matrix ? JSON.stringify(matrix, null, 2) : "// Run a completed batch to populate"}
          </pre>
        </div>
      </section>
    </div>
  );
}

function RunProgress({ scanned, total, votes }: { scanned: number; total: number; votes: number }) {
  const hasDenominator = total > 0;
  const pct = hasDenominator ? Math.min(100, Math.round((scanned / total) * 100)) : null;
  const label = scanned === 0
    ? "Starting…"
    : `${scanned.toLocaleString()} / ${hasDenominator ? total.toLocaleString() : "?"} docs${votes > 0 ? ` · ${votes.toLocaleString()} votes` : ""}`;

  return (
    <div className="flex min-w-[220px] flex-col gap-1">
      <div className="flex items-baseline justify-between text-xs text-ink-400">
        <span>{label}</span>
        {pct !== null ? <span className="font-mono">{pct}%</span> : null}
      </div>
      {hasDenominator ? (
        <progress
          className="h-1.5 w-full overflow-hidden rounded-full bg-ink-800 [&::-moz-progress-bar]:bg-accent-500 [&::-webkit-progress-bar]:bg-ink-800 [&::-webkit-progress-value]:bg-accent-500"
          value={scanned}
          max={total}
        />
      ) : (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-800">
          <div className="h-full w-1/3 animate-pulse rounded-full bg-accent-500" />
        </div>
      )}
    </div>
  );
}
