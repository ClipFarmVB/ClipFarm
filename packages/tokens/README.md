# @clipfarm/tokens

Colour, type, spacing and radius as plain data. One definition, read by both
`web/` and `mobile/`.

Plain data means exactly that: no React, no DOM, no `next/*`. The package is
consumed as TypeScript source (`exports` points at `src/index.ts`), so there is
no build step and no `dist/` to keep in sync — Turbopack, Vite and Metro all
transpile it in place.

```ts
import { colors, radius, spacing, fontSize } from "@clipfarm/tokens";

colors.dark.brand;   // "#f59e0b"
colors.light.brand;  // "#2563eb"
radius.xl;           // 12
spacing.md;          // 16
fontSize.sm;         // 14
```

## What is in here

| Module | Exports |
|---|---|
| `colors.ts` | `colors.light` / `colors.dark` (semantic slots), `palette` (fixed hues), `danger`, `actionColors` |
| `typography.ts` | `fontSize`, `lineHeight`, `fontWeight`, `letterSpacingEm`, `letterSpacingPx()`, `fontFamilyStack`, `fontFamilyName` |
| `spacing.ts` | `spacingUnit`, `space(n)`, `spacing` |
| `radius.ts` | `radius`, `borderWidth` |
| `css.ts` | `themeCss()` — the web stylesheet, see below |

The values were extracted from `web/src/app/globals.css` and the components
around it as they stood before CF-316. This is the palette the app already
ships; nothing here is new, with one exception noted below.

## How web consumes it

Web is on Tailwind v4, which has **no JavaScript config file** — the theme is
declared in CSS with `@theme`. So the package emits the CSS instead of being
imported by a config:

```
packages/tokens/src/css.ts   themeCss()
        ↓                    rendered into
web/src/app/tokens.generated.css
        ↓                    @import-ed by
web/src/app/globals.css
```

`web/src/app/tokens.generated.test.ts` holds the generated file as a vitest
file snapshot, so a token changed here without regenerating fails `npm run test
--workspace=web` — in the pre-commit hook and in CI.

After editing a colour:

```bash
npm run tokens:sync --workspace=web   # rewrites tokens.generated.css
```

Commit the token change and the regenerated CSS together.

**Only colour and the font stack cross into CSS.** Spacing, the type scale and
radius are Tailwind's own defaults on web, and the values in this package
mirror them 1:1 (`spacing.md` is 16px, which is what `p-4` gives you).
Re-declaring them in `@theme` would be an appearance-change risk in exchange
for nothing, so the package documents the scale rather than redefining it.

## How mobile consumes it

Directly — `import { colors } from "@clipfarm/tokens"` and read the numbers.
See `packages/ui-mobile`, which is built entirely out of them.

## The one new token

`onBrand` (`--cf-on-brand`, Tailwind `text-on-brand`). Web's Button had
`text-[#0c0c0e]` hardcoded for the label on a brand fill; it is the same value
in both themes, and mobile needs it too. Same colour, now named.

## Adding a token

1. Add it to the right module with a doc comment saying what it is *for*, not
   what colour it is — `brand` is blue in light mode and amber in dark.
2. If web should reach it as a CSS variable or a Tailwind utility, add it to
   `CSS_VARIABLE` / `TAILWIND_UTILITY` / `GROUPS` in `css.ts`.
3. `npm run tokens:sync --workspace=web`, and commit the regenerated file.
