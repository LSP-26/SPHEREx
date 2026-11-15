import { readFileSync } from "node:fs";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the page talks to the Python API through this proxy, so there is no CORS and
// EventSource works on the same origin. Which API it talks to, in order:
//   1. VITE_API_PROXY, if set;
//   2. a server running on this machine (python -m backend.server, port 8000);
//   3. the shared server whose address is in remote-api.txt (committed, so `git pull` is enough).
const LOCAL_API = "http://127.0.0.1:8000";

async function pickApi(): Promise<string> {
  if (process.env.VITE_API_PROXY) return process.env.VITE_API_PROXY;
  try {
    await fetch(`${LOCAL_API}/health`, { signal: AbortSignal.timeout(500) });
    return LOCAL_API;
  } catch {
    // No local server: fall through to the shared one.
  }
  try {
    const remote = readFileSync(new URL("./remote-api.txt", import.meta.url), "utf8").trim();
    if (remote) {
      console.log(`[vite] no local API on :8000, using the shared server ${remote}`);
      return remote;
    }
  } catch {
    // No remote-api.txt: keep the local default.
  }
  return LOCAL_API;
}

const ROUTES = [
  "/flipbook", "/frames", "/health", "/fixtures", "/targets", "/overlay", "/difference",
  "/movers", "/measure", "/flags", "/coverage",
];

/** For a static deploy (vite build): the live server the page should call, unless VITE_API_BASE says
 *  otherwise. The shared server's address is kept in remote-api.txt, so redeploying picks up a new one. */
function buildApiBase(): string {
  if (process.env.VITE_API_BASE !== undefined) return process.env.VITE_API_BASE;
  try {
    return readFileSync(new URL("./remote-api.txt", import.meta.url), "utf8").trim();
  } catch {
    return "";
  }
}

export default defineConfig(async ({ command }) => {
  if (command === "build") {
    return {
      // TOMBAUGH_BASE=/some/path/ for a site served from a sub-path (default: the domain root).
      base: process.env.TOMBAUGH_BASE ?? "/",
      plugins: [react(), tailwindcss()],
      define: { "import.meta.env.VITE_API_BASE": JSON.stringify(buildApiBase()) },
    };
  }
  const target = await pickApi();
  // changeOrigin: the tunnel routes by Host header, so it must be rewritten to the target's.
  const proxy = Object.fromEntries(ROUTES.map((r) => [r, { target, changeOrigin: true }]));
  return {
    plugins: [react(), tailwindcss()],
    server: { proxy },
    test: {
      environment: "jsdom",
      include: ["src/**/*.test.{ts,tsx}"],
      exclude: ["src/**/*.e2e.test.ts", "node_modules/**"],
    },
  };
});
