import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { ActivityIndicator, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";

import { SessionProvider, useSession } from "@/lib/session";

// Imported for its side effect: it is what calls `configureApiClient`, and the
// api client throws on the first request until something does. This is the one
// module the app cannot start without loading, which is why the binding hangs
// here rather than off whichever screen happens to fetch first. `routes.test.ts`
// fails if this import goes away.
import "@/lib/api";

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
 * tickets from colliding in one file.
 *
 * A screen added later does need a line here, with one exception. expo-router
 * registers any file under `src/app/`, declared or not, and an undeclared one
 * lands *outside* both `Stack.Protected` blocks — reachable while signed out.
 * Inside `(tabs)` or `(auth)` a new file inherits that group's guard and needs
 * nothing; anywhere else it needs its own entry.
 *
 * `routes.test.ts` holds the epic's screens to the block they belong in, not
 * merely to being mentioned somewhere in this file: a misspelled `name` guards
 * a route that does not exist and leaves the real one open, and a screen moved
 * out of its `Stack.Protected` block is reachable signed out. Both red there.
 * It cannot hold a screen nobody added — that is what the exception above is.
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
