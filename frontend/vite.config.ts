import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the page talks to the Python API through this proxy, so there is no CORS and
// EventSource works on the same origin. Start the API with: python -m backend.server
const api = process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/flipbook": api, "/frames": api, "/health": api, "/fixtures": api },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["src/**/*.e2e.test.ts", "node_modules/**"],
  },
});
