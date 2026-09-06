import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  View,
  type PressableProps,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import {
  borderWidth,
  danger as dangerColors,
  fontSize,
  letterSpacingPx,
  radius,
  spacing,
  type ThemeColors,
} from "@clipfarm/tokens";
import { Text } from "./Text";
import { useTheme } from "./theme";

/**
 * The mobile counterpart to web's Button, variant for variant.
 *
 * Web expresses hover; a phone has no hover, so every state web reaches on
 * hover this reaches on press instead. The two therefore land on the same
 * colours for the same meaning without either surface pretending to have the
 * other's input model.
 */

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

interface VariantColors {
  background: string;
  backgroundPressed: string;
  border: string;
  borderPressed: string;
  /** Label and spinner colour. */
  content: string;
  contentPressed: string;
}

function variantColors(
  variant: ButtonVariant,
  colors: ThemeColors,
): VariantColors {
  switch (variant) {
    case "primary":
      return {
        background: colors.brand,
        backgroundPressed: colors.brandLight,
        border: "transparent",
        borderPressed: "transparent",
        content: colors.onBrand,
        contentPressed: colors.onBrand,
      };
    case "secondary":
      return {
        background: colors.surfaceHigh,
        backgroundPressed: colors.surfaceHover,
        border: colors.border,
        borderPressed: colors.borderStrong,
        content: colors.foreground,
        contentPressed: colors.foreground,
      };
    case "ghost":
      return {
        background: "transparent",
        backgroundPressed: colors.surfaceHigh,
        border: "transparent",
        borderPressed: "transparent",
        content: colors.muted,
        contentPressed: colors.foreground,
      };
    case "danger":
      return {
        background: dangerColors.bg,
        backgroundPressed: dangerColors.bgHover,
        border: dangerColors.border,
        borderPressed: dangerColors.borderHover,
        content: dangerColors.fg,
        contentPressed: dangerColors.fg,
      };
  }
}

/** Padding and label size per size, mirroring web's px/py/text triples. */
const SIZES: Record<
  ButtonSize,
  { padding: ViewStyle; fontSize: number }
> = {
  sm: {
    padding: { paddingHorizontal: spacing.sm, paddingVertical: 6 },
    fontSize: fontSize.xs,
  },
  md: {
    padding: { paddingHorizontal: 14, paddingVertical: spacing.xs },
    fontSize: fontSize.sm,
  },
  lg: {
    padding: { paddingHorizontal: 20, paddingVertical: 10 },
    fontSize: fontSize.sm,
  },
};

const styles = StyleSheet.create({
  base: {
    borderRadius: radius.md,
    borderWidth,
  },
  // The label and spinner share a row so the spinner can sit on top of the
  // label without the two fighting over the button's own padding.
  content: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.xs,
  },
  // Web's `disabled:opacity-35`. Applied to the whole button so the border
  // fades with the label rather than the two drifting apart.
  disabled: { opacity: 0.35 },
  // Web's `active:scale-[0.97]`.
  pressed: { transform: [{ scale: 0.97 }] },
  // Keeps the button's width while the spinner is up: the label stays laid out
  // and is hidden, so a "Save" button does not collapse to a circle mid-request.
  hiddenLabel: { opacity: 0 },
  spinner: { position: "absolute" },
});

export interface ButtonProps extends Omit<PressableProps, "style" | "children"> {
  /** Button text. */
  label: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner in place of the label and blocks presses. */
  loading?: boolean;
  /** Stretches the button to the width of its container. */
  fullWidth?: boolean;
  style?: StyleProp<ViewStyle>;
}

export function Button({
  label,
  variant = "primary",
  size = "md",
  loading = false,
  fullWidth = false,
  disabled = false,
  style,
  ...props
}: ButtonProps) {
  const { colors } = useTheme();
  const palette = variantColors(variant, colors);
  const metrics = SIZES[size];
  // A loading button is inert: a second tap while the first request is in
  // flight is the most common way to double-post something.
  const inert = disabled || loading;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: inert, busy: loading }}
      accessibilityLabel={label}
      disabled={inert}
      style={({ pressed }) => [
        styles.base,
        metrics.padding,
        {
          backgroundColor: pressed ? palette.backgroundPressed : palette.background,
          borderColor: pressed ? palette.borderPressed : palette.border,
          alignSelf: fullWidth ? "stretch" : "flex-start",
        },
        pressed && !inert && styles.pressed,
        inert && styles.disabled,
        style,
      ]}
      {...props}
    >
      {({ pressed }) => (
        <View style={styles.content}>
          <Text
            variant="label"
            style={[
              {
                color: pressed ? palette.contentPressed : palette.content,
                fontSize: metrics.fontSize,
                letterSpacing: letterSpacingPx("tight", metrics.fontSize),
              },
              loading && styles.hiddenLabel,
            ]}
          >
            {label}
          </Text>
          {loading ? (
            <ActivityIndicator
              size="small"
              color={palette.content}
              style={styles.spinner}
            />
          ) : null}
        </View>
      )}
    </Pressable>
  );
}
