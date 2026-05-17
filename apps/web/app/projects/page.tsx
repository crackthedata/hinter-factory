"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { describeMlFetchError } from "@/lib/ml-fetch-error";
import { useProject, type Project } from "@/lib/project-context";

type ProjectCounts = {
  documents?: number;
  tags?: number;
  labeling_functions?: number;
  gold_labels?: number;
  lf_runs?: number;
  probabilistic_labels?: number;
};

type ProjectWithCounts = Project & { counts?: ProjectCounts };

export default function ProjectsPage() {
  const { projects, projectId, setProjectId, refresh, error: providerError } = useProject();
  const [details, setDetails] = useState<Record<string, ProjectCounts>>({});
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportingHinters, setExportingHinters] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadCounts = useCallback(async () => {
    const next: Record<string, ProjectCounts> = {};
    await Promise.all(
      projects.map(async (p) => {
        try {
          const res = await fetch(`/api/ml/v1/projects/${p.id}`);
          if (res.ok) {
            const data = (await res.json()) as ProjectWithCounts;
            if (data.counts) next[p.id] = data.counts;
          }
        } catch {
          /* ignore single failures */
        }
      }),
    );
    setDetails(next);
  }, [projects]);

  useEffect(() => {
    void loadCounts();
  }, [loadCounts]);

  const onCreate = async () => {
    setMessage(null);
    setError(null);
    const trimmed = newName.trim();
    if (!trimmed) {
      setError("Project name is required.");
      return;
    }
    setCreating(true);
    try {
      const res = await fetch("/api/ml/v1/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed, description: newDescription.trim() || null }),
      });
      const body = (await res.json()) as Project & { detail?: string };
      if (!res.ok) {
        setError(body.detail ?? `Could not create project (HTTP ${res.status}).`);
      } else {
        setMessage(`Created project "${body.name}".`);
        setNewName("");
        setNewDescription("");
        await refresh();
        setProjectId(body.id);
      }
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setCreating(false);
    }
  };

  const onDelete = async (project: Project) => {
    setMessage(null);
    setError(null);
    if (!window.confirm(`Delete project "${project.name}"? This removes all of its documents, tags, LFs, and labels.`)) {
      return;
    }
    try {
      const res = await fetch(`/api/ml/v1/projects/${project.id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        setError(body.detail ?? `Delete failed (HTTP ${res.status}).`);
        return;
      }
      setMessage(`Deleted project "${project.name}".`);
      await refresh();
    } catch (e) {
      setError(describeMlFetchError(e));
    }
  };

  const onExportHinters = async (project: Project) => {
    setMessage(null);
    setError(null);
    setExportingHinters(project.id);
    try {
      const res = await fetch(`/api/ml/v1/projects/${project.id}/export-hinters`);
      if (!res.ok) {
        setError(`Export hinters failed (HTTP ${res.status}).`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${slugify(project.name)}.hinters.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMessage(`Exported hinters for "${project.name}".`);
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setExportingHinters(null);
    }
  };

  const onExport = async (project: Project) => {
    setMessage(null);
    setError(null);
    setExporting(project.id);
    try {
      const res = await fetch(`/api/ml/v1/projects/${project.id}/export`);
      if (!res.ok) {
        setError(`Export failed (HTTP ${res.status}).`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${slugify(project.name)}.hinter-project.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMessage(`Exported "${project.name}".`);
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setExporting(null);
    }
  };

  const onImportFileChosen = async (file: File) => {
    setMessage(null);
    setError(null);
    setImporting(true);
    try {
      const text = await file.text();
      let bundle: unknown;
      try {
        bundle = JSON.parse(text);
      } catch {
        setError("File is not valid JSON.");
        return;
      }
      const res = await fetch("/api/ml/v1/projects/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bundle),
      });
      const body = (await res.json()) as ProjectWithCounts & { detail?: string };
      if (!res.ok) {
        setError(body.detail ?? `Import failed (HTTP ${res.status}).`);
        return;
      }
      setMessage(`Imported "${body.name}".`);
      await refresh();
      setProjectId(body.id);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (e) {
      setError(describeMlFetchError(e));
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-white">Projects</h1>
        <p className="mt-2 max-w-2xl text-sm text-ink-500">
          A project is a self-contained workspace: documents, tags, labeling functions, gold labels,
          and LF runs all live inside it. Switch projects with the picker in the header. Use Export
          to share a project as a single JSON file; Import accepts a previously exported file.
        </p>
      </div>

      <section className="rounded-lg border border-ink-900 bg-ink-900/30 p-4">
        <div className="text-sm font-medium text-white">Create project</div>
        <div className="mt-3 grid max-w-2xl gap-3 sm:grid-cols-[1fr_2fr]">
          <label className="block text-xs text-ink-500">
            Name
            <input
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 text-sm text-white outline-none ring-accent-500 focus:ring-2"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. Finance triage v2"
            />
          </label>
          <label className="block text-xs text-ink-500">
            Description (optional)
            <input
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 text-sm text-white outline-none ring-accent-500 focus:ring-2"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              placeholder="Short note about this project"
            />
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={creating || !newName.trim()}
            onClick={() => void onCreate()}
            className="rounded-md bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {creating ? "Creating…" : "Create"}
          </button>
          <span className="text-xs text-ink-500">
            New projects start empty. Select one in the header to begin ingesting.
          </span>
        </div>
      </section>

      <section className="rounded-lg border border-ink-900 bg-ink-900/30 p-4">
        <div className="text-sm font-medium text-white">Import project</div>
        <p className="mt-1 text-xs text-ink-500">
          Pick a JSON file previously produced by Export. UUIDs are re-minted on the way in, so the
          import never collides with existing data; the project name gets a suffix if it&apos;s
          already taken.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            disabled={importing}
            className="block w-full max-w-md text-sm text-ink-200 file:mr-4 file:rounded-md file:border-0 file:bg-accent-600 file:px-3 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-accent-500"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onImportFileChosen(f);
            }}
          />
          {importing ? <span className="text-xs text-ink-500">Importing…</span> : null}
        </div>
      </section>

      {message ? <div className="text-xs text-emerald-400">{message}</div> : null}
      {error ? <div className="text-xs text-red-400">{error}</div> : null}
      {providerError ? <div className="text-xs text-red-400">{providerError}</div> : null}

      <section className="overflow-hidden rounded-lg border border-ink-900">
        <table className="min-w-full divide-y divide-ink-900 text-left text-sm">
          <thead className="bg-ink-900/50 text-xs uppercase tracking-wide text-ink-500">
            <tr>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Documents</th>
              <th className="px-3 py-2">Tags</th>
              <th className="px-3 py-2">LFs</th>
              <th className="px-3 py-2">Gold</th>
              <th className="px-3 py-2">Runs</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-900">
            {projects.map((p) => {
              const c = details[p.id] ?? {};
              const isActive = projectId === p.id;
              return (
                <tr key={p.id} className={isActive ? "bg-accent-600/10" : undefined}>
                  <td className="px-3 py-2 align-top">
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-white">{p.name}</span>
                        {isActive ? (
                          <span className="rounded bg-accent-600/40 px-1.5 py-0.5 text-[10px] uppercase text-white">
                            active
                          </span>
                        ) : null}
                      </div>
                      {p.description ? (
                        <span className="text-xs text-ink-500">{p.description}</span>
                      ) : null}
                      <span className="font-mono text-[10px] text-ink-500">{p.id}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2 align-top text-ink-200">{c.documents ?? "—"}</td>
                  <td className="px-3 py-2 align-top text-ink-200">{c.tags ?? "—"}</td>
                  <td className="px-3 py-2 align-top text-ink-200">{c.labeling_functions ?? "—"}</td>
                  <td className="px-3 py-2 align-top text-ink-200">{c.gold_labels ?? "—"}</td>
                  <td className="px-3 py-2 align-top text-ink-200">{c.lf_runs ?? "—"}</td>
                  <td className="px-3 py-2 align-top">
                    <div className="flex justify-end gap-2">
                      {!isActive ? (
                        <button
                          type="button"
                          onClick={() => setProjectId(p.id)}
                          className="rounded border border-ink-700 bg-ink-950 px-2 py-1 text-xs text-ink-200 hover:border-accent-500"
                        >
                          Switch
                        </button>
                      ) : null}
                      <button
                        type="button"
                        disabled={exporting === p.id}
                        onClick={() => void onExport(p)}
                        className="rounded border border-ink-700 bg-ink-950 px-2 py-1 text-xs text-ink-200 hover:border-accent-500 disabled:opacity-50"
                      >
                        {exporting === p.id ? "Exporting…" : "Export project"}
                      </button>
                      <button
                        type="button"
                        disabled={exportingHinters === p.id}
                        onClick={() => void onExportHinters(p)}
                        className="whitespace-nowrap rounded border border-ink-700 bg-ink-950 px-2 py-1 text-xs text-ink-200 hover:border-accent-500 disabled:opacity-50"
                      >
                        {exportingHinters === p.id ? "Exporting…" : "Export hinters"}
                      </button>
                      <button
                        type="button"
                        onClick={() => void onDelete(p)}
                        className="rounded border border-red-700/40 bg-red-700/10 px-2 py-1 text-xs text-red-200 hover:bg-red-700/20"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {!projects.length ? (
              <tr>
                <td className="px-3 py-3 text-xs text-ink-500" colSpan={7}>
                  No projects yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function slugify(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "project"
  );
}
