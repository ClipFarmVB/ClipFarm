import { describe, expect, it, vi } from "vitest";

// `./session` imports react-native for `AppState` and reaches Supabase through
// `./supabase`; neither loads under this runner, and `signOutThisDevice` is
// handed its client, so both are inert stand-ins.
vi.mock("react-native", () => ({ AppState: {} }));
vi.mock("./supabase", () => ({ getSupabaseClient: () => ({}) }));

const { signOutThisDevice } = await import("./session");

describe("signOutThisDevice", () => {
  it("revokes the session everywhere when it can", async () => {
    const signOut = vi.fn(async () => ({ error: null }));

    await signOutThisDevice({ signOut } as never);

    expect(signOut).toHaveBeenCalledTimes(1);
    expect(signOut).toHaveBeenCalledWith();
  });

  it("still signs this device out when the revoke call fails", async () => {
    // Offline, supabase-js returns the error from a global sign-out and keeps
    // the local session — the user taps Sign out and stays signed in.
    const signOut = vi.fn(async (options?: { scope?: string }) => ({
      error: options?.scope === "local" ? null : new Error("Failed to fetch"),
    }));

    await expect(signOutThisDevice({ signOut } as never)).resolves.toBeUndefined();

    expect(signOut).toHaveBeenLastCalledWith({ scope: "local" });
  });

  it("fails when even the local sign-out fails", async () => {
    const signOut = vi.fn(async () => ({ error: new Error("keychain unavailable") }));

    await expect(signOutThisDevice({ signOut } as never)).rejects.toThrow(
      "keychain unavailable"
    );
  });
});
