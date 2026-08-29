// Placeholder for CF-328 (#378) — Sign in.
//
// Unlike the other placeholders this one works, because CF-315's own acceptance
// is that an existing ClipFarm account signs in and the session survives a cold
// start — which cannot be shown without somewhere to type a password. It is the
// session wiring proving itself, not the screen: CF-328 replaces this file with
// the real one, and owns the sign-up flow and the shared error wording from
// `authError.ts` that this deliberately does not duplicate.
import { Link } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Button,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useSession } from "@/lib/session";

export default function SignInScreen() {
  const { signIn } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim(), password);
      // No navigation here on purpose: the root layout's guard swaps the
      // signed-out stack for the signed-in one as soon as the session lands.
    } catch (caught) {
      // Supabase's own wording for now. CF-328 renders these through the
      // sentences the web app already shows.
      setError(caught instanceof Error ? caught.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>ClipFarm</Text>
      <TextInput
        style={styles.input}
        value={email}
        onChangeText={setEmail}
        placeholder="Email"
        autoCapitalize="none"
        autoComplete="email"
        keyboardType="email-address"
        inputMode="email"
      />
      <TextInput
        style={styles.input}
        value={password}
        onChangeText={setPassword}
        placeholder="Password"
        autoCapitalize="none"
        autoComplete="current-password"
        secureTextEntry
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {busy ? (
        <ActivityIndicator />
      ) : (
        <Button title="Sign in" onPress={submit} disabled={!email || !password} />
      )}
      <Link href="/signup" style={styles.link}>
        Create an account
      </Link>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", padding: 24, gap: 12 },
  title: { fontSize: 24, fontWeight: "600", textAlign: "center", marginBottom: 12 },
  input: { borderWidth: 1, borderRadius: 6, padding: 12, fontSize: 16 },
  error: { fontSize: 14 },
  link: { fontSize: 15, textAlign: "center", textDecorationLine: "underline" },
});
