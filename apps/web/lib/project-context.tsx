"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export type Project = {
  id: string;
  name: string;
  description?: string | null;
  created_at: string;
};

// See docs/notes-web.md (apps/web/lib/project-context.tsx section) for hasActiveProject gating semantics.
type ProjectContextValue = {
  projects: Project[];
  projectId: string | null;
  project: Project | null;
  hasActiveProject: boolean;
  setProjectId: (id: string) => void;
  refresh: () => Promise<void>;
  loading: boolean;
  error: string | null;
};

const ProjectContext = createContext<ProjectContextValue | null>(null);

const STORAGE_KEY = "hinter-factory.activeProjectId";

let _activeProjectId: string | null = null;

// See docs/notes-web.md (apps/web/lib/project-context.tsx section) for module-level cache rationale.
export function getActiveProjectId(): string | null {
  if (_activeProjectId) return _activeProjectId;
  if (typeof window !== "undefined") {
    return window.localStorage.getItem(STORAGE_KEY);
  }
  return null;
}

export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const initialised = useRef(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/ml/v1/projects");
      if (!res.ok) {
        setError(`Failed to load projects (HTTP ${res.status})`);
        setProjects([]);
        return;
      }
      const data = (await res.json()) as Project[];
      setProjects(data);
      const stored =
        typeof window !== "undefined" ? window.localStorage.getItem(STORAGE_KEY) : null;
      const candidate = stored && data.some((p) => p.id === stored) ? stored : data[0]?.id ?? null;
      if (candidate !== projectId) {
        setProjectIdState(candidate);
        _activeProjectId = candidate;
        if (candidate && typeof window !== "undefined") {
          window.localStorage.setItem(STORAGE_KEY, candidate);
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load projects");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (initialised.current) return;
    initialised.current = true;
    void refresh();
  }, [refresh]);

  const setProjectId = useCallback((id: string) => {
    setProjectIdState(id);
    _activeProjectId = id;
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, id);
    }
  }, []);

  const value = useMemo<ProjectContextValue>(() => {
    const project = projects.find((p) => p.id === projectId) ?? null;
    return {
      projects,
      projectId,
      project,
      hasActiveProject: project !== null,
      setProjectId,
      refresh,
      loading,
      error,
    };
  }, [projects, projectId, setProjectId, refresh, loading, error]);

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProject(): ProjectContextValue {
  const ctx = useContext(ProjectContext);
  if (!ctx) {
    throw new Error("useProject must be used within ProjectProvider");
  }
  return ctx;
}
