/**
 * CF-314: the query string `getClips` sends, pinned.
 *
 * The extraction changed one runtime line in the client: `getClips` builds its
 * query with `URLSearchParams.append` instead of `.set`, because React Native
 * implements the one and throws on the other. `platformSurface.test.ts` stops a
 * `set` coming back, but nothing else looked at what the function sends — a
 * renamed key (`sort` → `order`) or a dropped filter passed every suite, and
 * the game detail screen would have filtered and sorted on nothing.
 *
 * The expected strings are what the web client produced before the move
 * (`web/src/lib/api.ts` at the CF-314 merge base): same keys, same order, same
 * encoding. The cases after the first are the branches that leave a key out.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getClips } from "./api";
import { configureApiClient } from "./config";

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

beforeEach(() => {
  configureApiClient({ baseUrl: "http://api.test", getToken: () => null });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("getClips", () => {
  it("sends every filter under the key and in the order the web client did", async () => {
    const fetchMock = stubFetch();

    await getClips("g1", {
      action_type: ["spike", "serve"],
      player_id: "p7",
      min_confidence: 0.5,
      min_score: 3,
      sort: "score",
      page: 2,
      page_size: 25,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Compared as one string, not key by key: the order and the encoding of
    // the comma in `action_type` are part of what must not change.
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/games/g1/clips" +
        "?action_type=spike%2Cserve&player_id=p7&min_confidence=0.5&min_score=3&sort=score&page=2&page_size=25",
    );
  });

  it("sends no query string at all when no filter is set", async () => {
    const fetchMock = stubFetch();

    await getClips("g1");

    // Not `/clips?`: a bare `?` is the arm of the `qs ? … : ""` that breaks.
    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/games/g1/clips");
  });

  it("leaves out min_score when it is 0", async () => {
    const fetchMock = stubFetch();

    await getClips("g1", { min_score: 0, sort: "time" });

    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/games/g1/clips?sort=time");
  });

  it("leaves out action_type when the list is empty", async () => {
    const fetchMock = stubFetch();

    await getClips("g1", { action_type: [], player_id: "p7" });

    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/games/g1/clips?player_id=p7");
  });

  it("keeps min_confidence when it is 0, unlike min_score", async () => {
    const fetchMock = stubFetch();

    await getClips("g1", { min_confidence: 0 });

    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/games/g1/clips?min_confidence=0");
  });
});
