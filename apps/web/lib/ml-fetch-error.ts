// See docs/notes-web.md (apps/web/lib/ml-fetch-error.ts section) for what "Failed to fetch" actually means here.
export function describeMlFetchError(error: unknown): string {
  const message = error instanceof Error ? error.message : "Request failed";
  if (message === "Failed to fetch") {
    return "Could not reach the ML API (is it running on port 8000?). From the repo root run `pnpm dev` or `pnpm --filter @hinter/ml dev`.";
  }
  return message;
}
