import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  experimental: {
    // reactCompiler: true,
    // dynamicIO: true,
    authInterrupts: true,
  },
  
};

export default nextConfig;
