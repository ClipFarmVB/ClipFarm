// CF-314: the web binding is the only place that tells the shared client where
// the api is and who is asking, so it is what "no behaviour change on web"
// rests on. These pin the two values it supplies by their effect on a real
// request, not by reading the config back: the request goes to
// `NEXT_PUBLIC_API_URL` (or `http://localhost:8000` when unset), carries the
// Supabase session's access token as a bearer, and carries no Authorization
// header at all when there is no session — which is what `getAuthHeaders` did
// when it lived in this file.
//
// The binding configures the client when it is first evaluated and reads the
// env var then, so each test sets the env and the session first and takes a
// fresh module instance with `vi.resetModules()` + `await import`.
import { afterEach, describe, expect, it, vi } from "vitest";

// Mutable so each test picks the session the binding's `getToken` will see.
let session: { access_token: string } | null = null;
vi.mock("@/lib/supabase", () => ({
  createClient: () => ({
    auth: { getSession: async () => ({ data: { session } }) },
  }),
}));

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

/** Evaluate the binding afresh and issue one representative request. */
async function getGamesThroughBinding() {
  vi.resetModules();
  const { getGames } = await import("./api");
  await getGames();
}

function sent(fetchMock: ReturnType<typeof stubFetch>) {
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, headers: init.headers as Record<string, string> };
}

afterEach(() => {
  session = null;
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("the web api binding", () => {
  it("sends a signed-in request to NEXT_PUBLIC_API_URL with the session's bearer", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.web.test");
    session = { access_token: "session-token" };
    const fetchMock = stubFetch();

    await getGamesThroughBinding();

    const { url, headers } = sent(fetchMock);
    expect(url).toBe("http://api.web.test/games");
    expect(headers.Authorization).toBe("Bearer session-token");
  });

  it("falls back to http://localhost:8000 when NEXT_PUBLIC_API_URL is unset", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", undefined);
    const fetchMock = stubFetch();

    await getGamesThroughBinding();

    expect(sent(fetchMock).url).toBe("http://localhost:8000/games");
  });

  it("sends no Authorization header when there is no session", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.web.test");
    session = null;
    const fetchMock = stubFetch();

    await getGamesThroughBinding();

    // Absent, not `Bearer null` or `Bearer undefined`.
    expect(sent(fetchMock).headers).not.toHaveProperty("Authorization");
  });
});
