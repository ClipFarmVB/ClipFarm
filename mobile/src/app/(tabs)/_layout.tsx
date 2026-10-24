import { Link, Tabs } from "expo-router";
import { Text } from "react-native";

import { SOCIAL_ENABLED } from "@/lib/env";

/**
 * A tab bar, not the web app's sidebar. Navigation is the one place the two
 * surfaces deliberately diverge (CF-316) — labels only for now; CF-316 brings
 * the icons and the styling.
 *
 * `href: null` hides a tab without removing the route, so the social screens
 * stay reachable by deep link for development while the flag is off, and are
 * not offered to a user whose API would 404 them (CF-338).
 */
export default function TabsLayout() {
  const socialHref = SOCIAL_ENABLED ? undefined : null;
  return (
    <Tabs>
      <Tabs.Screen
        name="games"
        options={{
          title: "Library",
          // The only route into /settings, and so the only route to sign out.
          // It cannot hang off the profile tab: that tab is social-gated, and
          // with `EXPO_PUBLIC_SOCIAL_ENABLED=false` — the documented default —
          // the bar is Library and Collections only, which would leave a signed
          // -in device with no way back out. Plain text on purpose; CF-316
          // brings the icon.
          headerRight: () => (
            <Link href="/settings" style={{ paddingHorizontal: 16 }}>
              <Text>Settings</Text>
            </Link>
          ),
        }}
      />
      <Tabs.Screen name="collections" options={{ title: "Collections" }} />
      <Tabs.Screen name="feed" options={{ title: "Feed", href: socialHref }} />
      <Tabs.Screen name="profile" options={{ title: "Profile", href: socialHref }} />
    </Tabs>
  );
}
