import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";
import { actionColors, radius, spacing } from "@clipfarm/tokens";
import { Text } from "./Text";

/**
 * The action chip that labels a clip — spike, serve, dig, set, block.
 *
 * Colours come from `actionColors` in the token package. Web's Badge does
 * *not* read it: it keeps its own `ACTION_STYLES` table of Tailwind class
 * names (`web/src/components/ui/Badge.tsx`). The two are hand-kept duplicates
 * that happen to agree today, and `web/src/components/ui/Badge.tokens.test.ts`
 * is what keeps them agreeing — it walks both tables and fails when either
 * side moves. Until web switches to the token values, "the same red in both
 * apps" is an invariant the test enforces, not one the import gives for free.
 *
 * Unlike the other primitives this one does not change with the theme, which
 * matches web — but not because the hues work on either background. They do
 * not. Composited over each page background and measured against the 10px
 * label (normal text for WCAG, so the bar is 4.5:1):
 *
 * | chip                    | on light `#ffffff` | on dark `#0c0c0e` |
 * |-------------------------|--------------------|-------------------|
 * | spike                   | 2.58:1             | 6.42:1            |
 * | serve                   | 2.01:1             | 8.24:1            |
 * | dig                     | 1.80:1             | 9.22:1            |
 * | set                     | 2.57:1             | 6.46:1            |
 * | block                   | 2.18:1             | 7.60:1            |
 * | unknown                 | 4.38:1             | 3.81:1            |
 * | removed / not_an_action | 1.89:1             | 2.20:1            |
 *
 * These are dark-theme hues: five of eight pass on dark and fail on light,
 * three fail on both. Web lands on dark by default and only reaches light when
 * the user flips `cf-theme` by hand, so the gap is rarely seen there; here
 * `ThemeProvider` follows `useColorScheme()`, so a phone in light mode gets
 * the light rendering on first launch. That is a known gap, not a settled
 * question — theming `actionColors` the way `ThemeColors` is themed is the
 * other option and is a deliberate design change.
 */

const styles = StyleSheet.create({
  badge: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: spacing.hairline + 2,
    borderRadius: radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  dot: { width: 4, height: 4, borderRadius: radius.pill },
});

export interface BadgeProps {
  /** The text shown. Uppercased by the `micro` type variant, not by this value. */
  label: string;
  /**
   * The action whose colours to use. Defaults to `label`, which is the common
   * case; pass it separately when the label is a display string.
   * Anything unrecognised falls back to the grey `unknown` chip.
   */
  action?: string;
  style?: StyleProp<ViewStyle>;
}

export function Badge({ label, action, style }: BadgeProps) {
  const tone = actionColors[action ?? label] ?? actionColors.unknown;
  // Mirrors web: the API's `not_an_action` reads as "removed" to a user.
  const display = label === "not_an_action" ? "removed" : label;

  return (
    <View style={[styles.badge, { backgroundColor: tone.bg }, style]}>
      <View style={[styles.dot, { backgroundColor: tone.dot }]} />
      <Text variant="micro" style={{ color: tone.fg }}>
        {display}
      </Text>
    </View>
  );
}
