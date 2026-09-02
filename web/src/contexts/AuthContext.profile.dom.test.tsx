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
const profile = (username: string) => ({ id: username, username, username_is_generated: false });

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
