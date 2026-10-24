import { Link, Stack } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

/** Also the landing spot for a deep link to something that no longer exists. */
export default function NotFound() {
  return (
    <>
      <Stack.Screen options={{ title: "Not found" }} />
      <View style={styles.container}>
        <Text style={styles.title}>This screen does not exist.</Text>
        {/*
          `/` and not `/games`: the tabs live inside `Stack.Protected`, and a
          false guard removes those screens from the navigator rather than
          redirecting, so a signed-out viewer taps this and goes nowhere. `/`
          branches on the session (./index), which is the one route that is
          right either way — and a signed-out viewer is exactly who lands here
          once CF-326 starts routing deep links.
        */}
        <Link href="/" style={styles.link}>
          Take me back
        </Link>
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 12 },
  title: { fontSize: 17, fontWeight: "600" },
  link: { fontSize: 15, textDecorationLine: "underline" },
});
