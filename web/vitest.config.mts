import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Mirrors the `@/*` path in tsconfig.json so tests resolve imports the way
  // the app does.
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    // Everything covered so far is pure logic. Add `jsdom` here (and the
    // dependency) when the first component test arrives.
    environment: "node",
    // `.tsx` is included so the first component test runs instead of being
    // silently skipped — the existing suites would still match, so vitest would
    // report all green rather than "no test files found".
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
