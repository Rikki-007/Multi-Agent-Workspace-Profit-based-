import type { NextConfig } from "next";

// Static export: every agency site ships as plain files (CDN/GitHub Pages friendly, prerendered HTML).
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
};

export default nextConfig;
