import type { ReactElement, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The Settings screen's Sign out button (CF-315 placeholder, CF-343 replaces it).
 *
 * It lives here rather than beside the screen because anything under
 * `src/app/` is a route. There is no React Native renderer in this runner, so
 * react-native is stood in by named function components and the screen is
 * called as a function: it uses no hooks of its own beyond `useSession`, which
 * is mocked, so what comes back is the element tree it would render, and
 * "tapping" the button is calling the `onPress` that tree carries.
 */
const signOut = vi.fn<() => Promise<void>>();
const alert = vi.fn();

vi.mock("react-native", () => ({
  Alert: { alert: (...args: unknown[]) => alert(...args) },
  Button: function Button() {
    return null;
  },
  StyleSheet: { create: <T,>(styles: T) => styles },
  Text: function Text() {
    return null;
  },
  View: function View() {
    return null;
  },
}));

vi.mock("@/lib/session", () => ({
  useSession: () => ({ signOut }),
}));

const { default: SettingsScreen } = await import("@/app/settings/index");
const { Button } = await import("react-native");

type Element = ReactElement<{ children?: ReactNode; title?: string; onPress?: () => unknown }>;

/** Every element in the tree whose type is `type`, depth first. */
function findAll(node: ReactNode, type: unknown): Element[] {
  if (Array.isArray(node)) return node.flatMap((child) => findAll(child, type));
  if (!node || typeof node !== "object" || !("type" in node)) return [];
  const element = node as Element;
  return [
    ...(element.type === type ? [element] : []),
    ...findAll(element.props.children, type),
  ];
}

function signOutButton(): Element {
  const buttons = findAll(SettingsScreen(), Button).filter(
    (button) => button.props.title === "Sign out"
  );
  expect(buttons).toHaveLength(1);
  return buttons[0];
}

beforeEach(() => {
  signOut.mockReset();
  alert.mockReset();
});

describe("settings screen", () => {
  it("signs the user out when Sign out is tapped", async () => {
    signOut.mockResolvedValue(undefined);

    await signOutButton().props.onPress!();

    expect(signOut).toHaveBeenCalledTimes(1);
    expect(alert).not.toHaveBeenCalled();
  });

  it("says the user is still signed in when sign-out fails", async () => {
    // A rejection would otherwise be unhandled: the tap does nothing visible
    // and the user cannot tell that they are still signed in.
    signOut.mockRejectedValue(new Error("keychain unavailable"));

    await expect(signOutButton().props.onPress!()).resolves.toBeUndefined();

    expect(alert).toHaveBeenCalledWith(
      "You're still signed in",
      "Check your connection and try again."
    );
  });
});
