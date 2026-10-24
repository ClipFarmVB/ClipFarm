/**
 * Colour tokens.
 *
 * Every value here was lifted out of `web/src/app/globals.css` as it stood
 * before CF-316 — this is the palette the web app has been shipping, not a new
 * one. `themeCss()` (see `css.ts`) regenerates that stylesheet from this file,
 * so web keeps rendering exactly what it did and mobile gets the same numbers
 * without a second copy.
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
  /** Hover state for `brand`. */
  brandLight: string;
  /** Translucent `brand` wash behind an active row or a selected chip. */
  brandDim: string;
  /**
   * Text/icon colour on top of a `brand` fill. Near-black in both themes,
   * because that is the value web has been hardcoding in Button since the
   * first pass — this token names the shipped colour rather than choosing one.
   *
   * It is **not** the better label colour in light mode, and the comment that
   * used to live here claimed otherwise. Measured against the hexes in this
   * file (WCAG 2.x, `#0c0c0e` vs `#ffffff` as the label):
   *
   * - light `brand` `#2563eb` — 3.78:1 dark, 5.17:1 white
   * - light `brandLight` `#3b82f6` (press/hover) — 5.31:1 dark, 3.68:1 white
   * - dark `brand` `#f59e0b` — 9.10:1 dark
   *
   * So the resting state of every light-mode primary button is below AA's
   * 4.5:1 for the 12–14px labels that use it, and white would be better there
   * by a wide margin. Dark mode is comfortable. Switching light mode to
   * `#ffffff` is a visible appearance change on the login and upload CTAs and
   * wants its own card; until then, treat the light-mode value as inherited
   * from web and known to be sub-AA.
   */
  onBrand: string;

  /** Dot-grid pattern on the marketing page. */
  dot: string;
  /** Scrollbar thumb. */
  scrollbar: string;
  /** Scrollbar thumb, hovered. */
  scrollbarHover: string;
}

/** Light — white / off-white / blue. */
export const lightColors: ThemeColors = {
  background: "#ffffff",
  surface: "#f6f6f9",
  surfaceHigh: "#ededf2",
  surfaceHover: "#e4e4eb",

  border: "#e2e2ea",
  borderStrong: "#ccccd8",

  foreground: "#111116",
  muted: "#62627a",
  subtle: "#9898b4",

  brand: "#2563eb",
  brandLight: "#3b82f6",
  brandDim: "rgba(37, 99, 235, 0.09)",
  onBrand: "#0c0c0e",

  dot: "rgba(0, 0, 0, 0.10)",
  scrollbar: "#d4d4e0",
  scrollbarHover: "#b8b8cc",
};

/** Dark — charcoal / amber. */
export const darkColors: ThemeColors = {
  background: "#0c0c0e",
  surface: "#111114",
  surfaceHigh: "#18181c",
  surfaceHover: "#1e1e24",

  border: "#222226",
  borderStrong: "#333338",

  foreground: "#f0f0f2",
  muted: "#6e6e7a",
  subtle: "#44444e",

  brand: "#f59e0b",
  brandLight: "#fbbf24",
  brandDim: "rgba(245, 158, 11, 0.12)",
  onBrand: "#0c0c0e",

  dot: "rgba(255, 255, 255, 0.18)",
  scrollbar: "#2a2a2e",
  scrollbarHover: "#3a3a40",
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
