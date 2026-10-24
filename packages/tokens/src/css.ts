/**
 * Emits the colour tokens as the CSS web actually consumes.
 *
 * Web is on Tailwind v4, which has no JS config file — the theme is declared
 * in CSS with `@theme`. So "consumed via the Tailwind config" here means:
 * this function renders `web/src/app/tokens.generated.css`, globals.css
 * imports it, and a vitest file snapshot fails the build if the checked-in
 * copy drifts from what this returns. Regenerate with
 * `npm run tokens:sync --workspace=web`.
 *
 * Only colour and the font stack cross over. Spacing, type scale and radius
 * are Tailwind's own defaults on web (the token values mirror them 1:1), and
 * re-declaring them here would risk an appearance change for nothing.
 */

import {
  darkColors,
  lightColors,
  type ThemeColors,
} from "./colors";
import { fontFamilyStack } from "./typography";

/**
 * The `--cf-*` custom property each token is written to. Kept as an explicit
 * table rather than derived from the key, because these names predate the
 * package and renaming one would be an appearance change, not a refactor.
 */
const CSS_VARIABLE: Record<keyof ThemeColors, string> = {
  background: "--cf-bg",
  surface: "--cf-surface",
  surfaceHigh: "--cf-surface-high",
  surfaceHover: "--cf-surface-hover",
  border: "--cf-border",
  borderStrong: "--cf-border-strong",
  foreground: "--cf-fg",
  muted: "--cf-muted",
  subtle: "--cf-subtle",
  brand: "--cf-brand",
  brandLight: "--cf-brand-light",
  brandDim: "--cf-brand-dim",
  onBrand: "--cf-on-brand",
  dot: "--cf-dot",
  scrollbar: "--cf-scrollbar",
  scrollbarHover: "--cf-scrollbar-hover",
};

/**
 * The Tailwind colour utility each token is exposed as, where it has one.
 * The decoration tokens are deliberately absent: nothing addresses them
 * through a utility class, they are read with `var()` from globals.css.
 */
const TAILWIND_UTILITY: Partial<Record<keyof ThemeColors, string>> = {
  background: "background",
  surface: "surface",
  surfaceHigh: "surface-high",
  surfaceHover: "surface-hover",
  border: "border",
  borderStrong: "border-strong",
  foreground: "foreground",
  muted: "muted",
  subtle: "subtle",
  brand: "brand",
  brandLight: "brand-light",
  brandDim: "brand-dim",
  onBrand: "on-brand",
};

/**
 * Comments that ride along with a single `@theme` entry, where the token needs
 * a word the group label cannot carry.
 */
const TOKEN_NOTE: Partial<Record<keyof ThemeColors, readonly string[]>> = {
  onBrand: [
    "  /* Text and icons drawn on a brand fill (the primary button). Per theme,",
    "     because the light-theme teal is dark and the dark-theme teal is light. */",
  ],
};

/**
 * Why the palette is what it is, emitted between `@theme` and the values.
 *
 * Kept with the tokens rather than in globals.css because it is the reason the
 * numbers below are what they are: a reader who changes one needs these
 * figures, and mobile reads the same values with no stylesheet to find them in.
 */
const RATIONALE = `/*
 * Both themes take their accent from the logo (CF-573): a teal line-art mark
 * on cream. \`--cf-brand-light\` is the hover state of a brand fill, so in the
 * light theme it is *darker* than the base — a lighter teal would drop white
 * button text below AA.
 *
 * Contrast, WCAG relative luminance, measured when these were set (CF-568):
 *   light  on-brand #fff on brand #037170          5.83:1
 *          on-brand #fff on brand-light #025f5e    7.49:1
 *          brand #037170 as text on bg #faf7f1     5.46:1
 *          muted #65604f on bg                     5.88:1
 *   dark   on-brand #0b0f0f on brand #3cc2b3      8.77:1
 *          on-brand on brand-light #5fd3c5        10.66:1
 *          brand as text on bg #0b0f0f             8.77:1
 *          muted #7f8c8a on bg                     5.53:1
 * \`subtle\` stays near 3:1 in both, as before: it is for decorative and
 * tertiary text only.
 */`;

/** Emission order, and the comment each run of tokens sits under. */
const GROUPS: ReadonlyArray<{
  label: string;
  tokens: ReadonlyArray<keyof ThemeColors>;
}> = [
  { label: "Background layers", tokens: ["background", "surface", "surfaceHigh", "surfaceHover"] },
  { label: "Borders", tokens: ["border", "borderStrong"] },
  { label: "Text", tokens: ["foreground", "muted", "subtle"] },
  { label: "Accent", tokens: ["brand", "brandLight", "brandDim", "onBrand"] },
  { label: "Decoration", tokens: ["dot", "scrollbar", "scrollbarHover"] },
];

/** `  --name:   value;`, with values aligned across the whole block. */
function declarations(
  entries: ReadonlyArray<readonly [string, string]>,
  width: number,
): string[] {
  return entries.map(([name, value]) => `  ${`${name}:`.padEnd(width)}${value};`);
}

function widestName(names: ReadonlyArray<string>): number {
  // +1 for the colon, +1 so the widest line still has a space before its value.
  return Math.max(...names.map((n) => n.length)) + 2;
}

function themeBlock(): string {
  const utilities = GROUPS.map((group) => {
    const tokens = group.tokens.filter((token) => TAILWIND_UTILITY[token]);
    return {
      label: group.label,
      tokens,
      entries: tokens.map(
        (token) =>
          [`--color-${TAILWIND_UTILITY[token]}`, `var(${CSS_VARIABLE[token]})`] as const,
      ),
    };
  }).filter((group) => group.entries.length > 0);

  const width = widestName(
    utilities.flatMap((group) => group.entries.map(([name]) => name)),
  );

  const noted = (token: keyof ThemeColors, line: string) =>
    TOKEN_NOTE[token] ? [...TOKEN_NOTE[token]!, line] : [line];

  const body = utilities.map((group) => {
    const lines = group.entries.flatMap(([name, value], i) =>
      noted(group.tokens[i], declarations([[name, value]], width)[0]),
    );
    return [`  /* ${group.label} */`, ...lines].join("\n");
  });

  return [
    "@theme {",
    `  --font-sans: ${fontFamilyStack};`,
    "",
    "  /*",
    "   * All colour tokens reference CSS custom properties so they can be",
    "   * overridden by the .dark class on <html>.",
    "   */",
    "",
    body.join("\n\n"),
    "}",
  ].join("\n");
}

function paletteBlock(selector: string, heading: string, values: ThemeColors): string {
  const entries = GROUPS.flatMap((group) =>
    group.tokens.map((token) => [CSS_VARIABLE[token], values[token]] as const),
  );
  const width = widestName(entries.map(([name]) => name));

  const body = GROUPS.map((group) =>
    declarations(
      group.tokens.map((token) => [CSS_VARIABLE[token], values[token]] as const),
      width,
    ).join("\n"),
  );

  return [heading, `${selector} {`, body.join("\n\n"), "}"].join("\n");
}

/**
 * The two section rules, verbatim.
 *
 * Literal rather than padded to a column: the originals in globals.css are
 * hand-set (76 and 75 columns) and an em dash or a slash shifts where the eye
 * wants the break, so a formula reproduces neither and silently reflows them.
 */
const LIGHT_HEADING =
  "/* ── Light mode (default) — cream / warm neutrals / logo teal ────────── */";
const DARK_HEADING =
  "/* ── Dark mode — deep teal-black / bright logo teal ─────────────────── */";

/** The full contents of `web/src/app/tokens.generated.css`. */
export function themeCss(): string {
  return [
    "/* Generated from packages/tokens — do not edit.",
    "   Change the token package, then run `npm run tokens:sync --workspace=web`.",
    "   web/src/app/tokens.generated.test.ts fails the build if this drifts. */",
    "",
    themeBlock(),
    "",
    RATIONALE,
    "",
    paletteBlock(":root", LIGHT_HEADING, lightColors),
    "",
    paletteBlock(".dark", DARK_HEADING, darkColors),
    "",
  ].join("\n");
}
