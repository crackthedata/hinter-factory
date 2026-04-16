import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@hinter/contracts"],
  async rewrites() {
    return [{ source: "/api/ml/:path*", destination: "http://127.0.0.1:8000/:path*" }];
  },
};

export default nextConfig;
