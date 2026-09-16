import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";
import { actionColors, radius, spacing } from "@clipfarm/tokens";
import { Text } from "./Text";

/**
 * The action chip that labels a clip — spike, serve, dig, set, block.
 *
 * Colours come from the shared `actionColors` map, the same one web's Badge
 * reads, so a spike is the same red in both apps. Unlike the other primitives
 * this one does not change with the theme: the action hues are chosen to work
 * on either background, and web has always drawn them theme-independently.
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
