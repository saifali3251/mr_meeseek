import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_TARGET || process.env.VITE_API_TARGET || "https://34.47.179.98.sslip.io";

  return {
    plugins: [react()],
    base: "/console/",
    server: {
      host: true,
      port: 5174,
      allowedHosts: true,
      proxy: {
        "/ops": {
          target,
          changeOrigin: true,
          secure: false,
        },
        "/api": {
          target,
          changeOrigin: true,
          secure: false,
        },
        "/onboarding": {
          target,
          changeOrigin: true,
          secure: false,
        },
        "/leases": {
          target,
          changeOrigin: true,
          secure: false,
        },
        "/console/tasks": {
          target,
          changeOrigin: true,
          secure: false,
        },
      },
    },
  };
});

