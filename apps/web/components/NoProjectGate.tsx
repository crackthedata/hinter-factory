"use client";

import Link from "next/link";

import { useProject } from "@/lib/project-context";

// See docs/notes-web.md (apps/web/components/NoProjectGate.tsx section) for the loading vs warning branch behavior.
export function NoProjectGate({ pageName }: { pageName: string }) {
  const { loading, projects } = useProject();
  if (loading && !projects.length) {
    return (
      <div className="rounded-md border border-ink-800 bg-ink-900/30 p-4 text-sm text-ink-400">
        Loading projects…
      </div>
    );
  }
  return (
    <div className="space-y-4 rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
      <div>
        <span className="font-semibold">No active project.</span> {pageName} works on data
        scoped to a project.
      </div>
      {projects.length === 0 ? (
        <div>
          You don&apos;t have any projects yet.{" "}
          <Link href="/projects" className="font-medium text-accent-300 hover:text-accent-200 underline">
            Create one on the Projects page
          </Link>{" "}
          to begin.
        </div>
      ) : (
        <div>
          Pick a project from the header dropdown, or{" "}
          <Link href="/projects" className="font-medium text-accent-300 hover:text-accent-200 underline">
            manage projects
          </Link>
          .
        </div>
      )}
    </div>
  );
}
