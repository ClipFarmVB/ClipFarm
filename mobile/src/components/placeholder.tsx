import { StyleSheet, Text, View } from "react-native";

/**
 * A screen that exists so the route does (CF-315).
 *
 * Every screen in the mobile epic is registered up front, which is what lets a
 * screen ticket replace one file rather than edit a route table five other
 * tickets are also editing. Deliberately plain: CF-316 owns colour, spacing and
 * the shared primitives, and anything styled here would only have to be
 * unstyled again.
 */
export function Placeholder({
  screen,
  ticket,
  note,
}: {
  screen: string;
  /** The card that replaces this screen, e.g. "CF-329 (#379)". */
  ticket: string;
  note?: string;
}) {
  return (
    <View style={styles.container}>
      <Text style={styles.screen}>{screen}</Text>
      <Text style={styles.ticket}>{ticket}</Text>
      {note ? <Text style={styles.note}>{note}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 8 },
  screen: { fontSize: 20, fontWeight: "600" },
  ticket: { fontSize: 14, opacity: 0.6 },
  note: { fontSize: 13, opacity: 0.6, textAlign: "center" },
});
