import {
  StyleSheet,
  Text as RNText,
  type TextProps as RNTextProps,
  type TextStyle,
} from "react-native";
import {
  fontSize,
  fontWeight,
  letterSpacingPx,
  lineHeight,
} from "@clipfarm/tokens";
import { useTheme } from "./theme";

/**
 * Every string on screen goes through here.
 *
 * React Native's own Text inherits nothing — no family, no colour, no size —
 * so an unstyled Text renders 14px platform-default black and is invisible in
 * dark mode. That is the failure this primitive exists to prevent.
 */

export type TextVariant =
  | "display"
  | "title"
  | "heading"
  | "subheading"
  | "body"
  | "label"
  | "caption"
  | "micro";

/** Which semantic colour the text takes. `onBrand` is for text on a brand fill. */
export type TextTone = "default" | "muted" | "subtle" | "brand" | "onBrand";

const VARIANTS: Record<TextVariant, TextStyle> = {
  display: {
    fontSize: fontSize["3xl"],
    lineHeight: lineHeight["3xl"],
    fontWeight: fontWeight.bold,
    letterSpacing: letterSpacingPx("tight", fontSize["3xl"]),
  },
  title: {
    fontSize: fontSize["2xl"],
    lineHeight: lineHeight["2xl"],
    fontWeight: fontWeight.semibold,
    letterSpacing: letterSpacingPx("tight", fontSize["2xl"]),
  },
  heading: {
    fontSize: fontSize.lg,
    lineHeight: lineHeight.lg,
    fontWeight: fontWeight.semibold,
    letterSpacing: letterSpacingPx("tight", fontSize.lg),
  },
  subheading: {
    fontSize: fontSize.base,
    lineHeight: lineHeight.base,
    fontWeight: fontWeight.semibold,
  },
  body: {
    fontSize: fontSize.sm,
    lineHeight: lineHeight.sm,
    fontWeight: fontWeight.normal,
  },
  label: {
    fontSize: fontSize.sm,
    lineHeight: lineHeight.sm,
    fontWeight: fontWeight.medium,
  },
  caption: {
    fontSize: fontSize.xs,
    lineHeight: lineHeight.xs,
    fontWeight: fontWeight.normal,
  },
  // The uppercase micro-label on badges and section headers. `textTransform`
  // rather than uppercasing the string, so screen readers still get the word.
  micro: {
    fontSize: fontSize["2xs"],
    lineHeight: lineHeight["2xs"],
    fontWeight: fontWeight.semibold,
    letterSpacing: letterSpacingPx("widest", fontSize["2xs"]),
    textTransform: "uppercase",
  },
};

const styles = StyleSheet.create(VARIANTS);

export interface TextProps extends RNTextProps {
  /** Size, weight and tracking. Defaults to `body` (14px). */
  variant?: TextVariant;
  /** Colour. Defaults to `default` (the theme's primary foreground). */
  tone?: TextTone;
}

export function Text({
  variant = "body",
  tone = "default",
  style,
  ...props
}: TextProps) {
  const { colors, fontFamily } = useTheme();
  const toneColor = {
    default: colors.foreground,
    muted: colors.muted,
    subtle: colors.subtle,
    brand: colors.brand,
    onBrand: colors.onBrand,
  }[tone];

  return (
    <RNText
      // Caller style last: a screen overriding one property should not have to
      // restate the variant.
      style={[styles[variant], { color: toneColor, fontFamily }, style]}
      {...props}
    />
  );
}
