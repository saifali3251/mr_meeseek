import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/console/",
  server: {
    host: true,
    port: 5174,
    allowedHosts: true,
    proxy: {
      "/ops": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/onboarding": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/leases": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});

