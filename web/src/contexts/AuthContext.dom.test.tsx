// @vitest-environment jsdom
//
// CF-299: the games cache is cleared when the signed-in identity changes, and
// the clear lives here rather than in the sign-out button.
//
// This file exists because the unit tests on `gamesCache` could not see the
// call site at all: deleting the `clearGamesCache()` line from AuthContext left
// every one of them green, so the cache had a well-tested clear that nothing
// was proven to call. That is the shape of bug this repo keeps finding — the
// helper is pinned, the wiring is not.
//
// The account-switch case is the one the card missed. `/login` is reachable
// while signed in, so signing into a second account emits SIGNED_IN carrying a
// live session and never a null one; a clear gated on the event name, or on the
// session being null, does not fire and the incoming user's prefetch no-ops
// against a cache that is still fresh with the outgoing user's library.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  clearGamesCache: vi.fn(),
  clearMe: vi.fn(),
  prefetchGames: vi.fn(),
  emit: null as null | ((event: string, session: unknown) => void),
  initialSession: null as unknown,
}));

vi.mock("@/lib/gamesCache", () => ({
  clearGamesCache: mocks.clearGamesCache,
  prefetchGames: mocks.prefetchGames,
}));

vi.mock("@/lib/useMe", () => ({ clearMe: mocks.clearMe }));

vi.mock("@/lib/supabase", () => ({
  createClient: () => ({
    auth: {
      getSession: () => Promise.resolve({ data: { session: mocks.initialSession } }),
      onAuthStateChange: (cb: (event: string, session: unknown) => void) => {
        mocks.emit = cb;
        return { data: { subscription: { unsubscribe: () => {} } } };
      },
      signOut: () => Promise.resolve(),
    },
  }),
}));

import { AuthProvider } from "@/contexts/AuthContext";

const sessionFor = (id: string) => ({ user: { id } });

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.emit = null;
  mocks.initialSession = null;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** Mount the provider and settle the initial getSession() resolution. */
async function mount(initial: unknown = null) {
  mocks.initialSession = initial;
  await act(async () => {
    root.render(<AuthProvider>{null}</AuthProvider>);
  });
}

/** Deliver an auth-state change the way Supabase would. */
async function emit(event: string, session: unknown) {
  await act(async () => {
    mocks.emit?.(event, session);
  });
}

describe("AuthProvider — clearing the games cache on an identity change (CF-299)", () => {
  it("clears on sign-out", async () => {
    await mount(sessionFor("user-a"));
    mocks.clearGamesCache.mockClear();

    await emit("SIGNED_OUT", null);

    expect(mocks.clearGamesCache).toHaveBeenCalledTimes(1);
  });

  it("clears when another account signs in without a sign-out first", async () => {
    // No null session is ever delivered on this path.
    await mount(sessionFor("user-a"));
    mocks.clearGamesCache.mockClear();

    await emit("SIGNED_IN", sessionFor("user-b"));

    expect(mocks.clearGamesCache).toHaveBeenCalledTimes(1);
  });

  it("does not clear a warm cache on a token refresh", async () => {
    await mount(sessionFor("user-a"));
    mocks.clearGamesCache.mockClear();

    await emit("TOKEN_REFRESHED", sessionFor("user-a"));
    await emit("USER_UPDATED", sessionFor("user-a"));

    expect(mocks.clearGamesCache).not.toHaveBeenCalled();
  });

  it("does not clear on the first resolution, when there is nothing to drop", async () => {
    await mount(sessionFor("user-a"));

    expect(mocks.clearGamesCache).not.toHaveBeenCalled();
    expect(mocks.prefetchGames).toHaveBeenCalled();
  });

  it("clears the profile cache on the same identity change, not just the games one", async () => {
    // clearMe() is otherwise reachable only from the sign-out button, so the
    // switch path leaves `fetchMe()` returning the previous user's profile
    // from its `if (_me) return`. Clearing one cache and not the other here is
    // the same disclosure with a different noun.
    await mount(sessionFor("user-a"));
    mocks.clearMe.mockClear();

    await emit("SIGNED_IN", sessionFor("user-b"));

    expect(mocks.clearMe).toHaveBeenCalledTimes(1);
  });

  it("does not clear the profile cache on a token refresh", async () => {
    await mount(sessionFor("user-a"));
    mocks.clearMe.mockClear();

    await emit("TOKEN_REFRESHED", sessionFor("user-a"));

    expect(mocks.clearMe).not.toHaveBeenCalled();
  });

  it("survives a session whose user did not deserialise", async () => {
    // `session?.user.id` would throw here — before setLoading(false) — and
    // strand the app on the loading screen.
    await mount(sessionFor("user-a"));

    await expect(emit("SIGNED_IN", { user: undefined })).resolves.not.toThrow();
    expect(mocks.clearGamesCache).toHaveBeenCalledTimes(1);
  });

  it("does not prefetch when the session is gone", async () => {
    // Without the `if (session)` guard the sign-out itself fires a getGames()
    // as a signed-out user — a guaranteed 401, and one that repopulates
    // nothing, so it is pure noise on every sign-out.
    await mount(sessionFor("user-a"));
    mocks.prefetchGames.mockClear();

    await emit("SIGNED_OUT", null);

    expect(mocks.prefetchGames).not.toHaveBeenCalled();
  });

  it("clears before prefetching, or the incoming user gets no fetch at all", async () => {
    // prefetchGames() returns early while a promise from the previous identity
    // is in flight, so the order is the whole behaviour, not a tidiness point.
    await mount(sessionFor("user-a"));
    mocks.clearGamesCache.mockClear();
    mocks.prefetchGames.mockClear();

    await emit("SIGNED_IN", sessionFor("user-b"));

    const cleared = mocks.clearGamesCache.mock.invocationCallOrder[0];
    const prefetched = mocks.prefetchGames.mock.invocationCallOrder[0];
    expect(cleared).toBeLessThan(prefetched);
  });
});
