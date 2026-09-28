/**
 * Corner radius tokens, in px.
 *
 * These are Tailwind's `rounded-*` values for the steps web uses, so a mobile
 * card and a web card round identically.
 */
export const radius = {
  none: 0,
  /** 4px — `rounded-sm`. Badges and other small chips. */
  sm: 4,
  /** 6px — `rounded-md`. Buttons, inputs, skeleton blocks. */
  md: 6,
  /** 8px — `rounded-lg`. List rows. */
  lg: 8,
  /** 12px — `rounded-xl`. Cards. */
  xl: 12,
  /** 16px — `rounded-2xl`. Modals and sheets. */
  "2xl": 16,
  /**
   * Fully rounded. 999 rather than Tailwind's 9999: React Native clamps to
   * half the smaller side either way, and 9999 makes some Android versions
   * drop the corner entirely.
   */
  pill: 999,
} as const;

/** Hairline border width, in px. Matches web's default 1px `border`. */
export const borderWidth = 1;
