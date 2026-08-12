import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// Vitest rather than Jest: native ESM + TS, so it reads the existing
// tsconfig paths without a Babel layer. Next's own compiler is not
// involved here — @vitejs/plugin-react handles JSX for the tests.
export default defineConfig({
  plugins: [react()],
  // tsconfig.json sets `jsx: "preserve"` because Next owns the real build.
  // Left alone, the test transform falls back to the classic runtime and
  // every JSX file fails with "React is not defined". Next injects the
  // automatic runtime itself, so this only restates that for Vitest.
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: {
      // Mirrors tsconfig.json's `paths` so tests import the same way
      // application code does.
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // The Next build output and node_modules both contain compiled copies
    // of the source; without this Vitest would collect them twice.
    exclude: ["node_modules", ".next"],
    restoreMocks: true,
  },
});
