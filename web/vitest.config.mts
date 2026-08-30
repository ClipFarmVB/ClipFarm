import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Mirrors the `@/*` path in tsconfig.json so tests resolve imports the way
  // the app does.
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    // Most suites here are pure logic. The ones that need a DOM opt in with a
    // `// @vitest-environment jsdom` docblock of their own — there are now
    // four of them, counted rather than remembered: the focus-trap suite
    // (CF-227), PostComposerModal, PostGrid and BrandMark.
    // Left as `node` deliberately: a per-file opt-in keeps the rest off a
    // document they have no use for.
    environment: "node",
    // `.tsx` is included so the first component test runs instead of being
    // silently skipped — the existing suites would still match, so vitest would
    // report all green rather than "no test files found".
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
