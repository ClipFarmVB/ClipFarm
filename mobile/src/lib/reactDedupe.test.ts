import { createRequire } from "node:module";
import { dirname } from "node:path";
import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);

/**
 * One React in the tree, not two.
 *
 * npm nests a dependency it cannot dedupe, so mobile and web pinning different
 * React versions puts one copy at the repo root — where `react-native` imports
 * it from — and another under `mobile/`, where a screen imports it from. Both
 * end up in the bundle, the renderer installs the hooks dispatcher on one
 * React's internals and components read the other's, and the first `useState`
 * throws "Invalid hook call" on launch.
 *
 * Nothing else here catches that: lint, tsc and the rest of this suite never
 * bundle anything, and `metro.config.js` cannot fix it — its `nodeModulesPaths`
 * is a fallback for what Metro's upward walk missed, not an override for what
 * it found. So the invariant is the pin, and this is what holds it: if a future
 * bump moves `mobile/package.json`'s react off `web/package.json`'s, this fails
 * in CI rather than on a device.
 */
describe("react", () => {
  it("resolves to one copy from both react-native and app code", () => {
    const reactNativeDir = dirname(require.resolve("react-native/package.json"));

    const fromReactNative = require.resolve("react", { paths: [reactNativeDir] });
    const fromAppCode = require.resolve("react", { paths: [`${process.cwd()}/src/app`] });

    expect(fromAppCode).toBe(fromReactNative);
  });
});
