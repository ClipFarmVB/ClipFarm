# @clipfarm/tokens

Colour, type, spacing and radius as plain data. One definition, read by both
`web/` and `mobile/`.

Plain data means exactly that: no React, no DOM, no `next/*`. The package is
consumed as TypeScript source (`exports` points at `src/index.ts`), so there is
no build step and no `dist/` to keep in sync — Turbopack, Vite and Metro all
transpile it in place.

```ts
import { colors, radius, spacing, fontSize } from "@clipfarm/tokens";

colors.dark.brand;   // "#3cc2b3"
colors.light.brand;  // "#037170"
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

## `onBrand`, and why it is not new any more

`onBrand` (`--cf-on-brand`, Tailwind `text-on-brand`) was the single token this
package added when it was first written, to name the near-black label that
web's Button had hardcoded as an arbitrary value. While this branch sat, CF-568/CF-573 introduced the
same token on `main`, per theme and with the contrast measured — so the package
now mirrors it rather than proposing it.

That is a better answer than the one this package originally had. The label is
`#ffffff` on the light theme's dark teal (5.83:1) and `#0b0f0f` on the dark
theme's bright teal (8.77:1); both clear AA, where the old near-black-on-blue
measured 3.78:1 and did not. The figures travel with the stylesheet —
`themeCss()` emits CF-568's full table above the values.

## Adding a token

1. Add it to the right module with a doc comment saying what it is *for*, not
   what colour it is. Naming the hue is how a doc comment goes stale: the
   palette was blue and amber before CF-573 and every sentence that said so
   had to be found and rewritten.
2. If web should reach it as a CSS variable or a Tailwind utility, add it to
   `CSS_VARIABLE` / `TAILWIND_UTILITY` / `GROUPS` in `css.ts`.
3. `npm run tokens:sync --workspace=web`, and commit the regenerated file.
