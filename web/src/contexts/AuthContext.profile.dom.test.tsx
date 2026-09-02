// @vitest-environment jsdom
//
// CF-299: the profile half of the identity change, against the REAL `useMe`.
//
// The sibling file mocks `@/lib/useMe`, so it can only pin that `clearMe` is
// *called* — it would pass against a clearMe that did nothing, and it did pass
// against a version of this provider that cleared the profile and then left it
// blank forever. Nothing here is mocked below the auth boundary except the HTTP
// call itself.
//
// `useMe`'s effect is keyed on `enabled`, and on the switch path enabled never
// changes: the session goes A -> B with no null in between and `loading` is
// already false. So clearing alone publishes null and stops — no re-subscribe,
// no request. B is left with blank chrome, and because `needsHandle(null)` is
// false, a genuinely new B is never prompted to choose a handle. That is the
// same harm the clear was added to prevent, reached by the other side.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getMe: vi.fn(),
  emit: null as null | ((event: string, session: unknown) => void),
  initialSession: null as unknown,
}));

vi.mock("@/lib/api", () => ({ getMe: mocks.getMe }));
vi.mock("@/lib/gamesCache", () => ({ clearGamesCache: vi.fn(), prefetchGames: vi.fn() }));
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
import { clearMe, needsHandle, useMe } from "@/lib/useMe";

const sessionFor = (id: string) => ({ user: { id } });
const profile = (username: string, generated = false) => ({
  id: username,
  username,
  username_is_generated: generated,
});

/** A promise this test resolves or rejects itself, to hold a fetch open. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Renders whatever the shared profile cache currently holds. */
function Chrome() {
  const me = useMe(true);
  // `needsHandle` as well as the name: a blank profile answers `false` there,
  // so a genuinely new user is silently never prompted. That is the harm this
  // file exists for, and asserting only the handle string would miss it.
  return (
    <span data-testid="handle">
      {`${me?.username ?? "(none)"}|${needsHandle(me) ? "prompt" : "noprompt"}`}
    </span>
  );
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  // `useMe` is module state and this file does not reload the module between
  // tests, so without this the second test ever added here starts warm and
  // reads a profile the previous one fetched.
  clearMe();
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

const handle = () => container.querySelector('[data-testid="handle"]')?.textContent;

describe("AuthProvider + useMe — the profile cache across an identity change (CF-299)", () => {
  it("prompts a genuinely new user to choose a handle", async () => {
    // Without this case the `needsHandle` half of the assertion cannot fail:
    // every profile the file builds answers `false`, and so does `null`, so
    // `|noprompt` is constant across the passing and the broken state. The
    // harm being pinned is that a new B is *not* prompted, which only shows
    // against a B who should be.
    mocks.getMe.mockResolvedValueOnce(profile("alice"));
    mocks.initialSession = sessionFor("user-a");
    await act(async () => {
      root.render(
        <AuthProvider>
          <Chrome />
        </AuthProvider>,
      );
    });

    mocks.getMe.mockResolvedValueOnce(profile("user_x91", true));
    await act(async () => {
      mocks.emit?.("SIGNED_IN", sessionFor("user-b"));
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(handle()).toBe("user_x91|prompt");
  });

  it("keeps the incoming profile when the outgoing fetch rejects late", async () => {
    // The switch issues a second getMe() while the first is still open, so on
    // every switch there are two live requests and the orphaned one carries a
    // revoked token. `fetchMe`'s catch publishes null, and only its
    // generation guard stops that null landing on top of the user who just
    // arrived — blanking B by exactly the route this PR exists to close.
    // The concurrency is introduced here, so the guard is pinned here.
    const first = deferred<ReturnType<typeof profile>>();
    mocks.getMe.mockReturnValueOnce(first.promise);
    mocks.initialSession = sessionFor("user-a");
    await act(async () => {
      root.render(
        <AuthProvider>
          <Chrome />
        </AuthProvider>,
      );
    });

    mocks.getMe.mockResolvedValueOnce(profile("bob"));
    await act(async () => {
      mocks.emit?.("SIGNED_IN", sessionFor("user-b"));
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(handle()).toBe("bob|noprompt");

    // A's request finally fails, as a revoked token does.
    await act(async () => {
      first.reject(new Error("API error 401"));
      await Promise.resolve();
    });

    expect(handle()).toBe("bob|noprompt");
  });

  it("shows the incoming user's handle after a switch with no sign-out", async () => {
    mocks.getMe.mockResolvedValueOnce(profile("alice"));
    mocks.initialSession = sessionFor("user-a");
    await act(async () => {
      root.render(
        <AuthProvider>
          <Chrome />
        </AuthProvider>,
      );
    });
    expect(handle()).toBe("alice|noprompt");

    mocks.getMe.mockResolvedValueOnce(profile("bob"));
    await act(async () => {
      mocks.emit?.("SIGNED_IN", sessionFor("user-b"));
    });
    await act(async () => {
      await Promise.resolve();
    });

    // Not merely "not alice": blank is the failure this test exists for.
    expect(handle()).toBe("bob|noprompt");
  });
});
