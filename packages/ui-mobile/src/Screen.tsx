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
 * The root of every screen: theme background, safe-area inset on iOS,
 * status-bar glyph style, and the standard horizontal gutter.
 *
 * Nothing here knows about navigation, so it composes with whatever shell
 * CF-315 lands.
 *
 * Two platform caveats, both real today and both worth reading before building
 * a screen on this:
 *
 * 1. `SafeAreaView` from `react-native` is `Platform.select({ ios: native,
 *    default: View })` — on Android it is a plain `View` and pads nothing. So
 *    `safeArea` (default `true`) buys an inset on iOS and is a no-op on
 *    Android, where content draws under the status bar and the navigation bar.
 * 2. That import is **deprecated** in the pinned `react-native@0.81.4`: it
 *    carries `@deprecated`, logs a `warnOnce` on first mount ("SafeAreaView has
 *    been deprecated and will be removed in a future release. Please use
 *    'react-native-safe-area-context'"), and is scheduled for removal.
 *
 * Both are fixed by the same swap: `react-native-safe-area-context` arrives
 * with expo-router in CF-315, and `useSafeAreaInsets` here is the one place to
 * change — no screen has to. It is not a drop-in, though: it needs a
 * `SafeAreaProvider` above the tree and it changes Android's layout (that is
 * the point), so it is a deliberate change rather than a tidy-up.
 *
 * `StatusBar backgroundColor` below is likewise narrower than it looks — see
 * the comment at the call site.
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
  /**
   * Respect the safe-area inset **on iOS**. Turn off for a screen that draws
   * under the notch. No effect on Android, where `react-native`'s
   * `SafeAreaView` is a plain `View` — see the note at the top of this file.
   */
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
      {/* Dark theme means light glyphs, and vice versa — the naming inverts.
          `barStyle` is the part that works everywhere. `backgroundColor` is
          Android-only in RN 0.81.4's own types, and those types also say it
          "has no effect on Android 15+" — so it is a no-op on iOS and on
          current Android. Left in because it still paints on older Android,
          but do not count on Screen to tint the status bar. */}
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
