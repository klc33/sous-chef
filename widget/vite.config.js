// Vite build/dev config for the cook widget — plain JS/JSX React, no TypeScript (FR-022).
//
// The only plugin is @vitejs/plugin-react (JSX + Fast Refresh). Networking differs by mode:
//
//   • DEV (`npm run dev`): leave VITE_API_BASE EMPTY. api/client.js then uses relative paths ("/recipes",
//     "/profile", …) which Vite forwards to the backend through the SAME-ORIGIN dev proxy below. This means
//     dev works with zero CORS setup and regardless of how you open the app (localhost / 127.0.0.1 / LAN IP).
//     The proxy target is VITE_DEV_PROXY_TARGET (default http://localhost:8000).
//
//   • BUILD (production image): VITE_API_BASE is baked as an ABSOLUTE backend origin and inlined into the
//     bundle; the browser calls that origin directly (the proxy is dev-only), so the backend must allow the
//     widget's origin via CORS (app/config.py widget_origins).
//
// Vite does NOT read the repo-root .env — only widget/.env*. That is why an unset VITE_API_BASE used to make
// the widget call its own dev origin and fail ("Couldn't reach the kitchen"); the proxy removes that trap.

import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The cook endpoints the backend serves at its root. Vite proxies by path PREFIX, so "/recipes" also covers
// "/recipes/{id}" and "/favorites" covers "/favorites/{id}". Forwarded verbatim (no path rewrite).
const PROXIED_PATHS = ["/profile", "/recipes", "/favorites", "/chat", "/health"];

export default defineConfig(({ mode }) => {
  // Read widget/.env* (empty prefix = all vars) so the proxy target is configurable without code change.
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_DEV_PROXY_TARGET || "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      // changeOrigin rewrites the Host header to the backend so it accepts the proxied request.
      proxy: Object.fromEntries(
        PROXIED_PATHS.map((p) => [p, { target, changeOrigin: true }]),
      ),
    },
    preview: {
      host: "0.0.0.0",
      port: 4173,
    },
  };
});
