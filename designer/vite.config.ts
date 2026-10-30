// PHASE-S1 / APP-SHELL — Vite config for the CQL on Databricks SPA (README.md).
//
// The SPA is client-only in S1 (mock catalog, no engine round-trips). The Node BFF
// (src/server.ts, PHASE-S0) is a separate Node entrypoint and is not part of this bundle —
// Vite only bundles what is reachable from index.html -> /src/main.tsx.

/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Keep the SPA on its own port so it can run alongside the backend; proxy /api to it so the authoring
  // diagnostics loop (APP-DIAGNOSTICS) + runs reach the backend in dev exactly as in the deployed App
  // (same-origin /api). The Python/FastAPI backend defaults to PORT 3000 (`python -m cql_databricks_designer`).
  server: {
    port: 5173,
    // Proxy to the LITERAL IPv4 loopback, not "localhost". `python -m cql_databricks_designer` binds 127.0.0.1
    // (IPv4-only) for local dev, but Node resolves "localhost" to ::1 (IPv6) first — so a "localhost"
    // target makes every /api call hit ECONNREFUSED ::1:3000, and those proxy errors take down the dev
    // server after the SPA starts polling /api. 127.0.0.1 matches the backend's actual bind.
    proxy: { "/api": { target: "http://127.0.0.1:3000", changeOrigin: true } },
  },
  build: { outDir: "dist" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Only the SPA's own component tests; never the Node BFF files.
    include: ["src/**/*.test.tsx"],
  },
});
