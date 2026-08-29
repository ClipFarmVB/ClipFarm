import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Mirrors the `@/*` path in tsconfig.json so tests resolve imports the way
  // the app does.
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    // Node, and no react-native transform: everything covered here is the
    // contract logic — the secure-store adapter, the upload mock, the clip
    // source — which is deliberately written against injected dependencies so
    // it can be exercised without a device or a native module. Screen tests
    // arrive with the screens, and will need jest-expo or a RN-aware runner.
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}", "modules/**/*.test.ts"],
  },
});
