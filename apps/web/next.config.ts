import type { NextConfig } from "next";

// See docs/notes-web.md (apps/web/next.config.ts section) for the upload-cap rationale and rewrite invariants.
const nextConfig: NextConfig = {
  transpilePackages: ["@hinter/contracts"],
  experimental: {
    middlewareClientMaxBodySize: "4gb",
  },
  async rewrites() {
    return [{ source: "/api/ml/:path*", destination: "http://127.0.0.1:8000/:path*" }];
  },
};

export default nextConfig;
