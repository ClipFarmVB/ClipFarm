import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

/**
 * The api-client binding in `./api`, held by what it does rather than by how
 * it is spelled.
 *
 * `./api` configures the client as an import side effect. A text match on the
 * call stayed green with the call commented out (`config` null, the first
 * request throws) and with `getToken: () => null` (every request
 * unauthenticated), so this imports the real module and asserts on the
 * request it sends. Only the two things that cannot load under this runner are
 * stood in: Supabase's client, at the boundary `./session` reads the token
 * through, and react-native, which `./session` imports for `AppState`.
 * `./env` and `./session` themselves are the real modules, so the base URL is
 * what `API_URL()` yields and the token is what `getAccessToken` returns.
 *
 * Asserting through the request rather than on `getApiClientConfig()` is also
 * what keeps this holding through the CF-314 (#418) swap, when `./apiClient`
 * becomes the package and its config getter may move.
 */
const SESSION_TOKEN = "session-access-token";
const API_ROOT = "https://api.clipfarm.example";

vi.mock("react-native", () => ({
  AppState: { currentState: "active", addEventListener: () => ({ remove() {} }) },
}));

vi.mock("./supabase", () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: { access_token: SESSION_TOKEN } } }),
    },
  }),
}));

const requests: { url: string; headers: Headers }[] = [];

beforeAll(() => {
  // A release build, so `API_URL()` reads the variable rather than defaulting.
  vi.stubGlobal("__DEV__", false);
  vi.stubEnv("EXPO_PUBLIC_API_URL", API_ROOT);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url, headers: new Headers(init?.headers) });
      return new Response(null, { status: 204 });
    })
  );
});

afterAll(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("api binding", () => {
  it("sends a request to the configured api with the session's token", async () => {
    const { apiRequest } = await import("./api");

    await apiRequest("/games");

    expect(requests).toHaveLength(1);
    expect(requests[0].url).toBe(`${API_ROOT}/games`);
    expect(requests[0].headers.get("Authorization")).toBe(`Bearer ${SESSION_TOKEN}`);
  });
});
