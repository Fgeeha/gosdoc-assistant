import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// В dev запросы /api проксируются на локальный backend.
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 2000 },
  server: { proxy: { "/api": process.env.API_URL ?? "http://localhost:8000" } },
});
