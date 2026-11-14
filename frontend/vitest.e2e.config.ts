import { defineConfig } from "vitest/config";

// Talks to a REAL API server: E2E_API=http://127.0.0.1:8000 npm run e2e
export default defineConfig({
  test: { environment: "node", include: ["src/**/*.e2e.test.ts"], testTimeout: 30000 },
});
