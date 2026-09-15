import { StyleSheet, View, type ViewProps } from "react-native";
import { borderWidth, radius, spacing } from "@clipfarm/tokens";
import { Text } from "./Text";
import { useTheme } from "./theme";

/**
 * A raised panel. Same geometry as web's Card — `rounded-xl border p-4` — so a
 * clip tile reads the same on both surfaces.
 */

const styles = StyleSheet.create({
  card: {
    borderRadius: radius.xl,
    borderWidth,
    padding: spacing.md,
  },
  // Web's `overflow-hidden` variant: needed whenever a thumbnail runs to the
  // card's edge, because iOS does not clip a child to a rounded parent unless
  // the parent says so.
  flush: { padding: 0, overflow: "hidden" },
  header: { marginBottom: spacing.sm },
});

export interface CardProps extends ViewProps {
  /**
   * Drop the padding and clip children to the corners. For cards whose first
   * child is edge-to-edge media.
   */
  flush?: boolean;
}

export function Card({ flush = false, style, ...props }: CardProps) {
  const { colors } = useTheme();

  return (
    <View
      style={[
        styles.card,
        { backgroundColor: colors.surface, borderColor: colors.border },
        flush && styles.flush,
        style,
      ]}
      {...props}
    />
  );
}

/** Spacing wrapper for a card's title row. */
export function CardHeader({ style, ...props }: ViewProps) {
  return <View style={[styles.header, style]} {...props} />;
}

/** A card's title. `heading` role so VoiceOver can jump between cards. */
export function CardTitle({ children }: { children: string }) {
  return (
    <Text variant="subheading" accessibilityRole="header">
      {children}
    </Text>
  );
}
