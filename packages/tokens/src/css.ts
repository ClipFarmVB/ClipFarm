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
  const utilities = GROUPS.map((group) => ({
    label: group.label,
    entries: group.tokens.flatMap((token) => {
      const utility = TAILWIND_UTILITY[token];
      return utility
        ? [[`--color-${utility}`, `var(${CSS_VARIABLE[token]})`] as const]
        : [];
    }),
  })).filter((group) => group.entries.length > 0);

  const width = widestName(
    utilities.flatMap((group) => group.entries.map(([name]) => name)),
  );

  const body = utilities.map(
    (group) => [`  /* ${group.label} */`, ...declarations(group.entries, width)].join("\n"),
  );

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

/** A `── label ──` rule comment, padded to the 72-column width globals.css uses. */
function heading(label: string): string {
  const prefix = `/* ── ${label} `;
  return `${prefix}${"─".repeat(Math.max(3, 72 - prefix.length - 3))} */`;
}

/** The full contents of `web/src/app/tokens.generated.css`. */
export function themeCss(): string {
  return [
    "/* Generated from packages/tokens — do not edit.",
    "   Change the token package, then run `npm run tokens:sync --workspace=web`.",
    "   web/src/app/tokens.generated.test.ts fails the build if this drifts. */",
    "",
    themeBlock(),
    "",
    paletteBlock(":root", heading("Light mode (default) — white / off-white / blue"), lightColors),
    "",
    paletteBlock(".dark", heading("Dark mode — charcoal / amber"), darkColors),
    "",
  ].join("\n");
}
