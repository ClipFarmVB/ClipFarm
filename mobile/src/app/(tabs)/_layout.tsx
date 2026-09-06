import { Tabs } from "expo-router";

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
      <Tabs.Screen name="games" options={{ title: "Library" }} />
      <Tabs.Screen name="collections" options={{ title: "Collections" }} />
      <Tabs.Screen name="feed" options={{ title: "Feed", href: socialHref }} />
      <Tabs.Screen name="profile" options={{ title: "Profile", href: socialHref }} />
    </Tabs>
  );
}
