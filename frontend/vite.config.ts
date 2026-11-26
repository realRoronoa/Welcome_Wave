import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// xfwd: the API sees the caller's address in X-Forwarded-For (appended, so it is the entry uvicorn takes), and per-address
// rate limits apply per caller rather than to everyone at once as 127.0.0.1 (backend/app/ratelimit.py).
const target = { target: "http://localhost:8080", xfwd: true };
const proxy = { "/api": target, "/webhooks": target };

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
  // React gets its own chunk, shared by the site and the app, so neither surface's chunk carries a copy
  build: { rollupOptions: { output: { manualChunks(id) {
    if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return "react";
  } } } },
});
