"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useProject } from "@/lib/project-context";

export function ProjectSelector() {
  const { projects, projectId, setProjectId, loading } = useProject();
  const router = useRouter();

  if (loading && !projects.length) {
    return <div className="text-xs text-ink-500">Loading projects…</div>;
  }
  if (!projects.length) {
    return (
      <Link
        href="/projects"
        className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs font-medium text-amber-200 hover:bg-amber-500/20"
      >
        Create a project
      </Link>
    );
  }

  return (
    <label className="flex items-center gap-2 text-xs text-ink-500">
      <span className="hidden sm:inline">Project</span>
      <select
        value={projectId ?? ""}
        onChange={(e) => {
          setProjectId(e.target.value);
          // See docs/notes-web.md (apps/web/components/ProjectSelector.tsx section) for router.refresh() rationale.
          router.refresh();
        }}
        className="rounded-md border border-ink-700 bg-ink-950 px-2 py-1 text-sm text-white outline-none ring-accent-500 focus:ring-2"
      >
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}
