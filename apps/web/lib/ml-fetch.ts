// See docs/notes-web.md (apps/web/lib/ml-fetch.ts section) for mandatory-scoping + exempt-paths rationale.

import { getActiveProjectId } from "./project-context";

const ML_PREFIX = "/api/ml";
const SCOPED_EXEMPT_PATHS = ["/v1/projects"];

export class MissingProjectError extends Error {
  constructor() {
    super("No active project. Open the project picker in the header to choose or create one.");
    this.name = "MissingProjectError";
  }
}

export async function mlFetch(input: string, init?: RequestInit): Promise<Response> {
  const projectId = getActiveProjectId();
  let url = input;
  const body = init?.body;

  if (input.startsWith(ML_PREFIX)) {
    const pathPart = input.slice(ML_PREFIX.length);
    const isExempt = SCOPED_EXEMPT_PATHS.some((p) => pathPart.startsWith(p));
    if (!isExempt) {
      if (!projectId) throw new MissingProjectError();
      const u = new URL(input, "http://placeholder.local");
      if (!u.searchParams.has("project_id")) {
        u.searchParams.set("project_id", projectId);
      }
      url = `${u.pathname}${u.search}`;

      if (body instanceof FormData && !body.has("project_id")) {
        body.append("project_id", projectId);
      }
    }
  }

  return fetch(url, body !== init?.body ? { ...init, body } : init);
}
