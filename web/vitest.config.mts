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
    include: ["src/**/*.test.ts"],
  },
});
