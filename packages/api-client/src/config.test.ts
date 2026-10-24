/**
 * CF-314: the three promises the init contract makes, pinned.
 *
 * `configureApiClient` / `getApiClientConfig` are the only new runtime code in
 * the extraction, and they are what three other tickets in the CF-313 epic
 * build against — so the promises are load-bearing for consumers that do not
 * exist yet and cannot notice them breaking.
 *
 * Each of the three is a behaviour no other suite touches, and each has a
 * plausible, silent regression:
 *
 *   - Defaulting an unconfigured client to `http://localhost:8000` instead of
 *     throwing. A mobile build that forgot to initialise then spends its
 *     requests on a host that does not exist and reports network errors.
 *   - Reading `getToken` once instead of before every request. With the web
 *     binding handing over `supabase.auth.getSession()`, a memoised token means
 *     every request after the session refreshes goes out with a stale bearer
 *     and 401s — invisible to a suite that only ever issues one request.
 *   - Letting a rejecting `getToken` fail the request. A locked keychain or an
 *     unreadable token store should produce a signed-out request, which is what
 *     the web client did before this package existed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { getGames } from "./api";
import { configureApiClient } from "./config";

type FetchMock = ReturnType<typeof stubFetch>;

/** A `fetch` that answers every call with an empty JSON array, recording them. */
function stubFetch() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => [],
    text: async () => "[]",
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The headers `request()` assembled for the nth call — a plain object. */
function headersOf(fetchMock: FetchMock, call: number): Record<string, string> {
  const init = fetchMock.mock.calls[call][1] as RequestInit;
  return init.headers as Record<string, string>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("an unconfigured client", () => {
  // Both tests take a *fresh* module instance rather than the statically
  // imported one. `config` is a module-level singleton, so whatever the
  // `getToken` block below bound would otherwise still be in place, and these
  // are the two tests whose subject is the absence of a call — they cannot
  // share the registry with tests that configure. `vi.resetModules()` then
  // `await import` re-evaluates `api.ts` and the `config.ts` it imports
  // together, so the pair stays consistent; order between tests stops
  // mattering either way.

  it("throws a named error naming the call that fixes it", async () => {
    vi.resetModules();
    const { getApiClientConfig } = await import("./config");

    expect(() => getApiClientConfig()).toThrowError(/is not configured/);
    expect(() => getApiClientConfig()).toThrowError(/configureApiClient/);
  });

  it("throws on the first request instead of sending one somewhere", async () => {
    vi.resetModules();
    const fetchMock = stubFetch();
    const { getGames: freshGetGames } = await import("./api");

    await expect(freshGetGames()).rejects.toThrowError(/is not configured/);
    // The assertion that distinguishes "fails loudly" from a silent default: a
    // client that fell back to `http://localhost:8000` would also reject, just
    // later and as a network error, and the rejection check alone would pass.
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("getToken", () => {
  it("is called before every request rather than read once", async () => {
    const fetchMock = stubFetch();
    const tokens = ["first", "second"];
    const getToken = vi.fn(() => tokens.shift() ?? null);
    configureApiClient({ baseUrl: "http://api.test", getToken });

    await getGames();
    await getGames();

    expect(getToken).toHaveBeenCalledTimes(2);
    expect(headersOf(fetchMock, 0).Authorization).toBe("Bearer first");
    // This is the one that catches a memoised token. The call count above does
    // not: a wrapper could call `getToken` twice and still send the first
    // value, which in production is the stale bearer after a session refresh.
    expect(headersOf(fetchMock, 1).Authorization).toBe("Bearer second");
  });

  it("sends the request unauthenticated when it rejects", async () => {
    const fetchMock = stubFetch();
    configureApiClient({
      baseUrl: "http://api.test",
      getToken: () => Promise.reject(new Error("keychain locked")),
    });

    await expect(getGames()).resolves.toEqual([]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(headersOf(fetchMock, 0)).not.toHaveProperty("Authorization");
    // Still a well-formed request otherwise — the token is what is missing,
    // not the call.
    expect(headersOf(fetchMock, 0)["Content-Type"]).toBe("application/json");
  });

  it("sends no Authorization header at all when it returns null", async () => {
    const fetchMock = stubFetch();
    configureApiClient({ baseUrl: "http://api.test", getToken: () => null });

    await getGames();

    // Not `Bearer null`: signed out has to look signed out to the api, which
    // rejects a malformed bearer differently from an absent one.
    expect(headersOf(fetchMock, 0)).not.toHaveProperty("Authorization");
  });
});
