import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "@hinter/contracts";

import { MissingProjectError } from "./ml-fetch";
import { getActiveProjectId } from "./project-context";

// See docs/notes-web.md (apps/web/lib/api.ts section) for project_id injection + Request rebuild rationale.
const projectScopeMiddleware: Middleware = {
  async onRequest({ request }) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/ml/")) return undefined;
    if (url.pathname.startsWith("/api/ml/v1/projects")) return undefined;
    if (url.searchParams.has("project_id")) return undefined;

    const projectId = getActiveProjectId();
    if (!projectId) throw new MissingProjectError();
    url.searchParams.set("project_id", projectId);

    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const init: RequestInit = {
      method: request.method,
      headers: request.headers,
      credentials: request.credentials,
      cache: request.cache,
      redirect: request.redirect,
      referrer: request.referrer,
      integrity: request.integrity,
      mode: request.mode,
    };
    if (hasBody) {
      init.body = await request.clone().arrayBuffer();
    }
    return new Request(url.toString(), init);
  },
};

export const api = createClient<paths>({ baseUrl: "/api/ml" });
api.use(projectScopeMiddleware);
