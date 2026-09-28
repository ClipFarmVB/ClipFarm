/**
 * Type tokens.
 *
 * The scale is Tailwind's default, restricted to the steps web actually uses,
 * expressed in px so React Native can consume it directly. Web keeps reading
 * the scale out of Tailwind rather than out of this file — the numbers are the
 * same either way, and re-declaring `--text-*` in the generated stylesheet
 * would be a change in appearance risk for no gain. Only `--font-sans` crosses
 * over, because that one the app does define itself.
 */

/**
 * `Inter` with a system fallback, matching `--font-sans` in globals.css.
 *
 * React Native cannot take a stack: `fontFamily` is a single name, and the
 * platform substitutes on its own if the face is missing. So mobile uses
 * `fontFamilyName`, and only web gets the full stack.
 */
export const fontFamilyStack = '"Inter", ui-sans-serif, system-ui, sans-serif';
export const fontFamilyName = "Inter";

/** Font sizes in px. */
export const fontSize = {
  /** 10px — badge/chip text. Below Tailwind's scale; web writes `text-[10px]`. */
  "2xs": 10,
  /** 12px — `text-xs`. */
  xs: 12,
  /** 14px — `text-sm`. The default body size across the app. */
  sm: 14,
  /** 16px — `text-base`. */
  base: 16,
  /** 18px — `text-lg`. */
  lg: 18,
  /** 20px — `text-xl`. */
  xl: 20,
  /** 24px — `text-2xl`. */
  "2xl": 24,
  /** 30px — `text-3xl`. */
  "3xl": 30,
  /** 36px — `text-4xl`. */
  "4xl": 36,
} as const;

/** Line heights in px, paired 1:1 with `fontSize`. */
export const lineHeight: Record<keyof typeof fontSize, number> = {
  "2xs": 14,
  xs: 16,
  sm: 20,
  base: 24,
  lg: 28,
  xl: 28,
  "2xl": 32,
  "3xl": 36,
  "4xl": 40,
};

/**
 * Font weights.
 *
 * Strings because that is what React Native's `TextStyle["fontWeight"]` wants;
 * CSS accepts the same form.
 */
export const fontWeight = {
  normal: "400",
  medium: "500",
  semibold: "600",
  bold: "700",
} as const;

/**
 * Letter spacing in **em**, the unit CSS and this codebase's Tailwind classes
 * use. React Native's `letterSpacing` is absolute px, so mobile consumers go
 * through `letterSpacingPx()` rather than reading these directly.
 */
export const letterSpacingEm = {
  /** `tracking-tight` — headings and button labels. */
  tight: -0.025,
  normal: 0,
  /** `tracking-widest` — the uppercase micro-labels on badges. */
  widest: 0.1,
} as const;

/** Resolve an em-based tracking token against a font size, for React Native. */
export function letterSpacingPx(
  token: keyof typeof letterSpacingEm,
  size: number,
): number {
  return letterSpacingEm[token] * size;
}
