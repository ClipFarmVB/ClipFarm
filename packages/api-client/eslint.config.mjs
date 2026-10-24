import js from "@eslint/js";
import tseslint from "typescript-eslint";

// Its own eslint config, not web's. `eslint-config-next` is the right set for
// `web/` and the wrong one here: it carries React, JSX-a11y and Next rules for
// a package that renders nothing, and `next/core-web-vitals` assumes a Next
// app directory. What this package needs is the plain TypeScript set — unused
// symbols, `any`, unsafe syntax — so that is all it loads.
//
// Type-unaware on purpose (`configs.recommended`, not `recommendedTypeChecked`):
// `npm run typecheck` already runs tsc over exactly these files on the
// package's own tsconfig, so a type-aware lint run would re-do that work for a
// handful of extra rules, and would need its own `parserOptions.project` to
// stay in step with it.
const eslintConfig = tseslint.config(
  { ignores: ["node_modules/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
);

export default eslintConfig;
