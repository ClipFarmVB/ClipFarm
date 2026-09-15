/**
 * Spacing tokens.
 *
 * Tailwind's scale is `step * 4px`, and web is built on it, so the shared
 * model is the same: `space(n)` for anything that maps to a Tailwind class
 * (`p-3` is `space(3)`), and the named scale below for the handful of
 * distances that recur often enough to deserve a word.
 */

/** One spacing step, in px. Tailwind's `--spacing`, which is 0.25rem. */
export const spacingUnit = 4;

/** `space(3) === 12`, the same 12px `p-3` gives web. */
export function space(steps: number): number {
  return steps * spacingUnit;
}

/** Named distances, for the cases where a number reads as arbitrary. */
export const spacing = {
  none: 0,
  /** Hairline gap — a dot next to its label. */
  hairline: space(1),
  /** 8px — inside a chip, between an icon and its label. */
  xs: space(2),
  /** 12px — small button padding, tight list rows. */
  sm: space(3),
  /** 16px — card padding, the default gutter. */
  md: space(4),
  /** 24px — between cards, section padding. */
  lg: space(6),
  /** 32px — between major sections. */
  xl: space(8),
  /** 48px — page-level top/bottom breathing room. */
  "2xl": space(12),
} as const;
