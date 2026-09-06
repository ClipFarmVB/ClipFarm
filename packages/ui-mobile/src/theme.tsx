import {
  createContext,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { useColorScheme } from "react-native";
import { colors, type ThemeColors, type ThemeName } from "@clipfarm/tokens";

/**
 * Theme plumbing for the mobile primitives.
 *
 * Deliberately self-contained: it reads the OS setting and nothing else, so it
 * has no dependency on the app shell, navigation or session. If mobile later
 * grows a persisted preference the way web has (`cf-theme` in localStorage),
 * pass it down through `ThemeProvider name=...` — every primitive already
 * reads whatever the provider resolves.
 */

interface ThemeValue {
  name: ThemeName;
  colors: ThemeColors;
  /**
   * The face Text renders in, or undefined for the platform default.
   *
   * Not defaulted to `fontFamilyName` ("Inter") on purpose: React Native
   * resolves a font family by registered name, and naming one the app has not
   * loaded renders a fallback on iOS and nothing recognisable on Android. The
   * app shell loads Inter and passes it in; until it does, the primitives look
   * plain rather than broken.
   */
  fontFamily?: string;
}

/**
 * Dark by default, matching web's ThemeContext: `useColorScheme()` returns
 * null before the OS setting is known, and flashing light-then-dark is worse
 * than starting on the theme most of the app is designed around.
 */
const Ctx = createContext<ThemeValue>({ name: "dark", colors: colors.dark });

export function ThemeProvider({
  name,
  fontFamily,
  children,
}: {
  /** Force a theme. Omit to follow the OS. */
  name?: ThemeName;
  /** Registered font family for all Text. Pass once Inter is loaded. */
  fontFamily?: string;
  children: ReactNode;
}) {
  const scheme = useColorScheme();
  const resolved: ThemeName = name ?? (scheme === "light" ? "light" : "dark");
  const value = useMemo(
    () => ({ name: resolved, colors: colors[resolved], fontFamily }),
    [resolved, fontFamily],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** The active theme's colours. Safe to call outside a provider — defaults to dark. */
export function useTheme(): ThemeValue {
  return useContext(Ctx);
}
