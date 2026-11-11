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
 * it found.
 *
 * What this asserts is the invariant itself — react-native and app code see one
 * React — rather than "the two pins match". The pins drifting is the cause, but
 * whether it surfaces depends on which copy npm hoists: hoist mobile's and this
 * still passes while `web/` gets the nested one. Checking the resolution
 * catches the half that breaks the app.
 */
describe("react", () => {
  it("looks in the right place", () => {
    // The resolution below starts from `process.cwd()`, and from the repo root
    // both sides walk up to the hoisted React and agree no matter what is
    // nested under `mobile/` — a pass that proves nothing. `npm run test
    // --workspace=mobile` runs here; anything else has to.
    expect(process.cwd().endsWith("mobile")).toBe(true);
  });

  it("resolves to one copy from both react-native and app code", () => {
    const reactNativeDir = dirname(require.resolve("react-native/package.json"));

    const fromReactNative = require.resolve("react", { paths: [reactNativeDir] });
    const fromAppCode = require.resolve("react", { paths: [`${process.cwd()}/src/app`] });

    expect(fromAppCode).toBe(fromReactNative);
  });
});
