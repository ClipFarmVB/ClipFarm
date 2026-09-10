import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { ActivityIndicator, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";

import { SessionProvider, useSession } from "@/lib/session";

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SessionProvider>
        <RootNavigator />
        <StatusBar style="auto" />
      </SessionProvider>
    </GestureHandlerRootView>
  );
}

/**
 * The whole route table for the mobile epic (CF-315).
 *
 * Every screen is declared here once so that a screen ticket replaces its own
 * file and touches nothing shared — the single change that keeps five screen
 * tickets from colliding in one file. Adding a screen to an existing flow is
 * still just a new file; this list is only for the ones that need options or a
 * guard.
 *
 * `Stack.Protected` rather than a redirect in an effect: a guard is evaluated
 * before the screen mounts, so a signed-out cold start never flashes the
 * library on its way to the sign-in screen.
 */
function RootNavigator() {
  const { session, isRestoring } = useSession();

  // The stored session is read from the keychain asynchronously. Routing on
  // `session` before that resolves would send a signed-in user to sign-in on
  // every launch.
  if (isRestoring) {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <Stack>
      <Stack.Protected guard={!session}>
        <Stack.Screen name="(auth)" options={{ headerShown: false }} />
      </Stack.Protected>

      <Stack.Protected guard={!!session}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen
          name="upload"
          options={{ presentation: "modal", title: "Upload a game" }}
        />
        <Stack.Screen name="games/[gameId]" options={{ title: "Game" }} />
        <Stack.Screen name="collections/[collectionId]" options={{ title: "Collection" }} />
        <Stack.Screen name="clips/[clipId]/index" options={{ title: "Clip" }} />
        <Stack.Screen name="clips/[clipId]/edit" options={{ title: "Edit clip" }} />
        <Stack.Screen name="clips/[clipId]/trim" options={{ title: "Trim" }} />
        <Stack.Screen name="clips/[clipId]/tag" options={{ title: "Tag a player" }} />
        <Stack.Screen
          name="clips/[clipId]/share"
          options={{ presentation: "modal", title: "Share" }}
        />
        <Stack.Screen
          name="clips/[clipId]/post"
          options={{ presentation: "modal", title: "Post to feed" }}
        />
        <Stack.Screen name="u/[handle]/index" options={{ title: "Profile" }} />
        <Stack.Screen name="u/[handle]/followers" options={{ title: "Followers" }} />
        <Stack.Screen name="u/[handle]/following" options={{ title: "Following" }} />
        <Stack.Screen name="settings/index" options={{ title: "Settings" }} />
        <Stack.Screen name="settings/notifications" options={{ title: "Notifications" }} />
      </Stack.Protected>
    </Stack>
  );
}
