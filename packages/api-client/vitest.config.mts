import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Pure logic — no DOM, no jsdom. Same posture as web/'s suite.
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
