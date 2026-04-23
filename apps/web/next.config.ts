import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@hinter/contracts"],
  experimental: {
    // Next.js 15.5+ clones proxied request bodies into a PassThrough so
    // middleware/rewrites can read them, and caps that buffer at 10 MB by
    // default. Anything bigger is truncated mid-stream and the upstream
    // connection is closed (the client sees ECONNRESET, the dev console logs
    // "Request body exceeded 10MB"). Our /api/ml/v1/documents/upload route
    // has to accept multi-GB CSVs, so we lift the limit high enough to cover
    // them.
    //
    // The option is named `middlewareClientMaxBodySize` in 15.5.x (it was
    // renamed to `proxyClientMaxBodySize` in newer versions — bump both if
    // you upgrade Next.js). Value is parsed by the `bytes` package, so
    // "b/kb/mb/gb" all work.
    //
    // Trade-off: Next.js holds the body in V8 heap as it streams through.
    // For files larger than a few hundred MB you may also need to bump
    // Node's heap with NODE_OPTIONS=--max-old-space-size=4096 (or higher).
    // If you regularly ingest >2 GB files, prefer pointing the browser
    // straight at http://127.0.0.1:8000/v1/documents/upload to bypass this
    // proxy entirely.
    middlewareClientMaxBodySize: "4gb",
  },
  async rewrites() {
    // The /api/ml/* rewrite proxies multipart bodies straight through to the
    // FastAPI service. If you ever consider moving the upload behind a Next.js
    // route handler (`app/api/.../route.ts`) or server action, remember that
    // those default to buffering the entire request body in memory, which
    // will OOM the dev server on big files. Keep large uploads on this
    // rewrite path (and keep middlewareClientMaxBodySize above generous).
    return [{ source: "/api/ml/:path*", destination: "http://127.0.0.1:8000/:path*" }];
  },
};

export default nextConfig;
