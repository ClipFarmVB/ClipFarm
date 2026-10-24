/**
 * WCAG 2.x contrast arithmetic.
 *
 * Here because the package already owns every colour the app ships, and a
 * contrast figure written down beside them goes stale the moment one moves.
 * `themeCss()` computes the table it emits with these, so regenerating the
 * stylesheet cannot renew a measurement against values it was not measured
 * against. Mobile can use them too — the badge palette is a known light-mode
 * gap and a screen may want to check a pairing before it ships one.
 */

/** An sRGB triple, 0–255. */
export type Rgb = readonly [number, number, number];

/**
 * Parse `#rrggbb` or `rgba(r, g, b, a)` — the two forms the tokens use.
 * Returns the colour and its alpha; a hex is opaque.
 */
export function parseColor(value: string): { rgb: Rgb; alpha: number } {
  const fn = value.match(
    /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/,
  );
  if (fn) {
    return {
      rgb: [Number(fn[1]), Number(fn[2]), Number(fn[3])],
      alpha: fn[4] === undefined ? 1 : Number(fn[4]),
    };
  }

  const hex = value.trim().replace(/^#/, "");
  const full = hex.length === 3 ? hex.replace(/./g, (c) => c + c) : hex;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) {
    throw new Error(`parseColor: not a hex or rgb()/rgba() colour: ${value}`);
  }
  return {
    rgb: [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as unknown as Rgb,
    alpha: 1,
  };
}

/**
 * Flatten a possibly-translucent colour onto an opaque backdrop.
 *
 * Needed because several tokens are `rgba()` washes — `brandDim`, every
 * `actionColors` fill — and a ratio against a translucent colour is
 * meaningless until you say what is behind it.
 */
export function compositeOver(color: string, backdrop: string): Rgb {
  const { rgb, alpha } = parseColor(color);
  const base = parseColor(backdrop).rgb;
  return rgb.map((c, i) => c * alpha + base[i] * (1 - alpha)) as unknown as Rgb;
}

/** WCAG relative luminance. */
export function relativeLuminance(rgb: Rgb): number {
  const [r, g, b] = rgb.map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * Contrast ratio between two colours, each composited onto `backdrop` first
 * so translucent tokens measure as they render. 1 to 21; AA wants 4.5 for
 * normal text, 3 for large text and non-text.
 */
export function contrastRatio(
  foreground: string,
  background: string,
  backdrop = background,
): number {
  const fg = relativeLuminance(compositeOver(foreground, backdrop));
  const bg = relativeLuminance(compositeOver(background, backdrop));
  const [hi, lo] = fg >= bg ? [fg, bg] : [bg, fg];
  return (hi + 0.05) / (lo + 0.05);
}

/** `contrastRatio` rounded to the two decimals the docs quote. */
export function contrastLabel(
  foreground: string,
  background: string,
  backdrop = background,
): string {
  return `${contrastRatio(foreground, background, backdrop).toFixed(2)}:1`;
}
