import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `@vitejs/plugin-react` was already a dependency but had no config file to
// load it, so Fast Refresh was never actually running in dev.
export default defineConfig({
  plugins: [react()],
  server: {
    // Port is left at Vite's default (5173) to match the README's local-dev
    // instructions. The container overrides it with `--port 3000` so it lines
    // up with the mapping in docker-compose.yml.
    proxy: {
      // Lets the app call relative `/api/v1/...` paths, so no base URL is
      // hardcoded in the components and dev needs no CORS config on the
      // backend. VITE_API_TARGET points this at `http://backend:8000` when
      // running under Compose, where `localhost` is the frontend container.
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
