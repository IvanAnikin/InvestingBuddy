import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    lockDistDir: false,
  },
};

export default nextConfig;
