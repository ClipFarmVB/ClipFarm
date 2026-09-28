import {
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
  type ScrollViewProps,
} from "react-native";
import { spacing } from "@clipfarm/tokens";
import { useTheme } from "./theme";
import type { ReactNode } from "react";

/**
 * The root of every screen: theme background, safe-area inset, status-bar
 * colour, and the standard horizontal gutter.
 *
 * Nothing here knows about navigation, so it composes with whatever shell
 * CF-315 lands. Uses React Native's own `SafeAreaView`, which is iOS-only and
 * pads the top and bottom insets; when mobile picks up
 * `react-native-safe-area-context` (expo-router brings it) this is the one
 * place to swap for `useSafeAreaInsets`, and no screen has to change.
 */

const styles = StyleSheet.create({
  fill: { flex: 1 },
  gutter: { paddingHorizontal: spacing.md },
  // Lets the last row scroll clear of a tab bar or home indicator.
  scrollContent: { flexGrow: 1, paddingBottom: spacing.xl },
});

export interface ScreenProps {
  children: ReactNode;
  /** Wrap the content in a ScrollView. Off by default — a list screen brings its own. */
  scroll?: boolean;
  /** Standard horizontal gutter. Turn off for edge-to-edge content. */
  padded?: boolean;
  /** Respect the safe-area inset. Turn off for a screen that draws under the notch. */
  safeArea?: boolean;
  style?: StyleProp<ViewStyle>;
  /** Forwarded to the ScrollView when `scroll` is set. */
  scrollProps?: Omit<ScrollViewProps, "style" | "contentContainerStyle">;
}

export function Screen({
  children,
  scroll = false,
  padded = true,
  safeArea = true,
  style,
  scrollProps,
}: ScreenProps) {
  const { name, colors } = useTheme();
  const Container = safeArea ? SafeAreaView : View;

  return (
    <Container style={[styles.fill, { backgroundColor: colors.background }, style]}>
      {/* Dark theme means light glyphs, and vice versa — the naming inverts. */}
      <StatusBar
        barStyle={name === "dark" ? "light-content" : "dark-content"}
        backgroundColor={colors.background}
      />
      {scroll ? (
        <ScrollView
          style={styles.fill}
          contentContainerStyle={[styles.scrollContent, padded && styles.gutter]}
          {...scrollProps}
        >
          {children}
        </ScrollView>
      ) : (
        <View style={[styles.fill, padded && styles.gutter]}>{children}</View>
      )}
    </Container>
  );
}
