import { Link, Stack } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

/** Also the landing spot for a deep link to something that no longer exists. */
export default function NotFound() {
  return (
    <>
      <Stack.Screen options={{ title: "Not found" }} />
      <View style={styles.container}>
        <Text style={styles.title}>This screen does not exist.</Text>
        <Link href="/games" style={styles.link}>
          Go to your games
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
