// Placeholder for CF-343 (#393) — Settings.
//
// Unlike the other placeholders this one carries a working Sign out, because
// nothing else in the app can sign a user out, and a device left signed in has
// no way to switch accounts — the iOS keychain keeps the session even across a
// reinstall. CF-343 replaces this file with the real screen; the route and its
// options are already registered.
import { Alert, Button, StyleSheet, View } from "react-native";

import { Placeholder } from "@/components/placeholder";
import { useSession } from "@/lib/session";

export default function SettingsScreen() {
  const { signOut } = useSession();

  // No navigation here on purpose, as on sign-in: the root layout's guard swaps
  // the signed-in stack for the signed-out one as soon as the session clears.
  async function submit() {
    try {
      await signOut();
    } catch {
      Alert.alert(
        "You're still signed in",
        "Check your connection and try again."
      );
    }
  }

  return (
    <View style={styles.container}>
      <Placeholder
        screen="Settings"
        ticket="CF-343 (#393)"
        note="The Wifi-only upload toggle (CF-327) lives here too."
      />
      <View style={styles.actions}>
        <Button title="Sign out" onPress={submit} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  actions: { padding: 24 },
});
