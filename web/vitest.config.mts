import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Mirrors the `@/*` path in tsconfig.json so tests resolve imports the way
  // the app does.
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    // Everything here is pure logic except the focus-trap suite (CF-227), which
    // opts into jsdom with a `// @vitest-environment jsdom` docblock of its own.
    // Left as `node` deliberately: a per-file opt-in keeps the other seven
    // suites off a document they have no use for.
    environment: "node",
    // `.tsx` is included so the first component test runs instead of being
    // silently skipped — the existing suites would still match, so vitest would
    // report all green rather than "no test files found".
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
