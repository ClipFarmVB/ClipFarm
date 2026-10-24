/**
 * Colour tokens.
 *
 * Every value here comes out of `web/src/app/globals.css` — this is the
 * palette the web app ships, not a new one. The logo teal and the cream
 * neutrals are CF-573's; the contrast figures below them are CF-568's, both
 * measured on web before this package existed. `themeCss()` (see `css.ts`)
 * regenerates that stylesheet from this file, so web keeps rendering exactly
 * what it did and mobile gets the same numbers without a second copy.
 */

/** The two themes the app ships. Mirrors `Theme` in web's ThemeContext. */
export type ThemeName = "light" | "dark";

/**
 * Semantic colour slots. Named for the job, not the hue — `brand` is blue in
 * light mode and amber in dark, and nothing that consumes it needs to know.
 */
export interface ThemeColors {
  /** Page background, behind everything. */
  background: string;
  /** Raised panel: cards, list rows, the sidebar. */
  surface: string;
  /** A step above `surface`: inputs, secondary buttons, skeleton highlight. */
  surfaceHigh: string;
  /** Hover/press state for `surfaceHigh`. */
  surfaceHover: string;

  /** Default hairline border. */
  border: string;
  /** Border for a hovered or emphasised element. */
  borderStrong: string;

  /** Primary text. */
  foreground: string;
  /** Secondary text: metadata, inactive nav items. */
  muted: string;
  /** Tertiary text and icons; below this it is decoration, not content. */
  subtle: string;

  /** Accent — primary buttons, focus rings, active nav. */
  brand: string;
  /**
   * Hover/press state for a `brand` fill. In the light theme this is *darker*
   * than `brand`, not lighter: a lighter teal would drop the white label below
   * AA. In the dark theme it is brighter, as the name suggests.
   */
  brandLight: string;
  /** Translucent `brand` wash behind an active row or a selected chip. */
  brandDim: string;
  /**
   * Text/icon colour on a `brand` fill — the primary button's label.
   *
   * Per theme, because the two brands sit on opposite sides of the contrast
   * line: the light theme's teal is dark, so the label is white; the dark
   * theme's teal is bright, so the label is near-black. Both clear AA
   * comfortably (5.83:1 and 8.77:1) — `themeCss()` emits the full set of
   * figures CF-568 measured, and they travel with the stylesheet.
   */
  onBrand: string;

  /** Dot-grid pattern on the marketing page. */
  dot: string;
  /** Scrollbar thumb. */
  scrollbar: string;
  /** Scrollbar thumb, hovered. */
  scrollbarHover: string;
}

/** Light — cream / warm neutrals / logo teal. */
export const lightColors: ThemeColors = {
  background: "#faf7f1",
  surface: "#f3eee4",
  surfaceHigh: "#ebe4d7",
  surfaceHover: "#e3dbcc",

  border: "#e5ded1",
  borderStrong: "#d2c9b8",

  foreground: "#1c1b17",
  muted: "#65604f",
  subtle: "#948c7b",

  brand: "#037170",
  brandLight: "#025f5e",
  brandDim: "rgba(3, 113, 112, 0.09)",
  onBrand: "#ffffff",

  dot: "rgba(60, 50, 30, 0.12)",
  scrollbar: "#d9d1c2",
  scrollbarHover: "#c2b8a5",
};

/** Dark — deep teal-black / bright logo teal. */
export const darkColors: ThemeColors = {
  background: "#0b0f0f",
  surface: "#101616",
  surfaceHigh: "#172020",
  surfaceHover: "#1d2828",

  border: "#1f2a2a",
  borderStrong: "#2f3d3c",

  foreground: "#eef3f2",
  muted: "#7f8c8a",
  subtle: "#56625f",

  brand: "#3cc2b3",
  brandLight: "#5fd3c5",
  brandDim: "rgba(60, 194, 179, 0.12)",
  onBrand: "#0b0f0f",

  dot: "rgba(255, 255, 255, 0.16)",
  scrollbar: "#26302f",
  scrollbarHover: "#36423f",
};

export const colors: Record<ThemeName, ThemeColors> = {
  light: lightColors,
  dark: darkColors,
};

/**
 * Fixed hues that do not flip with the theme.
 *
 * These are Tailwind v4's own palette entries, converted from the oklch form
 * `tailwindcss/theme.css` ships to hex, because React Native cannot parse
 * oklch. Web keeps using the Tailwind utilities (`text-red-400` and friends);
 * these exist so mobile lands on the same pixels rather than on a guess.
 */
export const palette = {
  red400: "#ff6467",
  red500: "#fb2c36",
  sky400: "#00bcff",
  sky500: "#00a6f4",
  emerald400: "#00d492",
  emerald500: "#00bc7d",
  violet400: "#a684ff",
  violet500: "#8e51ff",
  orange400: "#ff8904",
  orange500: "#ff6900",
  zinc500: "#71717b",
  zinc600: "#52525c",
  zinc800: "#27272a",
} as const;

/** A tinted surface + its foreground, the shape every status chip needs. */
export interface ToneColors {
  /** Translucent fill. */
  bg: string;
  /** Text and icon colour on that fill. */
  fg: string;
  /** Border, where the component draws one. */
  border: string;
}

/**
 * Destructive affordances — the `danger` Button variant, delete confirmations.
 * Theme-independent, matching web: `bg-red-500/10 text-red-400
 * border-red-500/20`, and the /20 border again at /40 on hover.
 */
export const danger: ToneColors & { borderHover: string; bgHover: string } = {
  bg: "rgba(251, 44, 54, 0.10)",
  bgHover: "rgba(251, 44, 54, 0.20)",
  fg: palette.red400,
  border: "rgba(251, 44, 54, 0.20)",
  borderHover: "rgba(251, 44, 54, 0.40)",
};

/**
 * Per-action badge colours, matching `ACTION_STYLES` in web's Badge.
 *
 * Web does not import this — it renders badges with Tailwind class names, and
 * `themeCss()` emits only `ThemeColors`, so these values never reach
 * `tokens.generated.css` and the stylesheet snapshot cannot see them. The
 * match is held by `web/src/components/ui/Badge.tokens.test.ts`, which resolves
 * every class in `ACTION_STYLES` against `palette` and compares it to the entry
 * here. Move one side without the other and that test fails.
 *
 * Keyed by the `ActionType` values the API returns. `unknown` is the fallback
 * for anything unrecognised, so a new action type degrades to a grey chip
 * rather than an unstyled one. The hues are tuned for the dark theme; see the
 * measured table in `packages/ui-mobile/src/Badge.tsx`.
 */
export const actionColors: Record<string, ToneColors & { dot: string }> = {
  spike: { bg: "rgba(251, 44, 54, 0.08)", fg: palette.red400, border: "transparent", dot: palette.red400 },
  serve: { bg: "rgba(0, 166, 244, 0.08)", fg: palette.sky400, border: "transparent", dot: palette.sky400 },
  dig: { bg: "rgba(0, 188, 125, 0.08)", fg: palette.emerald400, border: "transparent", dot: palette.emerald400 },
  set: { bg: "rgba(142, 81, 255, 0.08)", fg: palette.violet400, border: "transparent", dot: palette.violet400 },
  block: { bg: "rgba(255, 105, 0, 0.08)", fg: palette.orange400, border: "transparent", dot: palette.orange400 },
  unknown: { bg: "rgba(113, 113, 123, 0.08)", fg: palette.zinc500, border: "transparent", dot: palette.zinc500 },
  removed: { bg: "rgba(39, 39, 42, 0.60)", fg: palette.zinc600, border: "transparent", dot: palette.zinc600 },
  not_an_action: { bg: "rgba(39, 39, 42, 0.60)", fg: palette.zinc600, border: "transparent", dot: palette.zinc600 },
};
