/**
 * CF-109b (#398): what `createPost` actually puts on the wire.
 *
 * `raise_clip_visibility` is the one field in this codebase whose failure mode
 * is "youth-sports footage silently becomes readable by other people". The
 * composer's own suite proves the checkbox gates the call — but it MOCKS
 * `@/lib/api`, so it observes the argument this function is handed and never
 * the body it builds. A review round showed both halves of that gap: defaulting
 * the parameter to `true`, and hardcoding the body's flag to `true`, each left
 * every one of the 203 tests green.
 *
 * So this asserts the request itself, `fetch` stubbed, the way
 * `getUserPosts.test.ts` does. The default is the load-bearing half: every
 * caller that has not been taught about the flag must send `false`, not
 * "nothing", because the API reads a missing field as its own default and
 * agreeing by luck is not the same as agreeing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createPost, setClipVisibility } from "./api";

function captureRequest() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ "content-length": "2" }),
    json: async () => ({}),
    text: async () => "{}",
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function bodyOf(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const init = fetchMock.mock.calls[0][1] as RequestInit;
  return JSON.parse(init.body as string);
}

beforeEach(() => {
  // getAuthHeaders reaches Supabase; irrelevant to the body being built.
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("createPost and the clip it may widen", () => {
  it("does not ask to widen the clip unless told to", async () => {
    const fetchMock = captureRequest();

    await createPost("clip-1", "nice dig", "followers");

    expect(bodyOf(fetchMock)).toEqual({
      clip_id: "clip-1",
      caption: "nice dig",
      visibility: "followers",
      raise_clip_visibility: false,
    });
  });

  it("asks to widen it only when the caller passes the flag", async () => {
    const fetchMock = captureRequest();

    await createPost("clip-1", "", "public", true);

    expect(bodyOf(fetchMock).raise_clip_visibility).toBe(true);
    expect(bodyOf(fetchMock).visibility).toBe("public");
  });

  it("sends the flag explicitly rather than omitting it", async () => {
    // Omitting it would work today, because the API's own default is false —
    // and it would keep working right up until that default changed, with
    // nothing on this side to notice. The two agreeing is the point; agreeing
    // by absence is not.
    const fetchMock = captureRequest();

    await createPost("clip-1", "", "private");

    expect(Object.keys(bodyOf(fetchMock))).toContain("raise_clip_visibility");
  });

  it("posts to /posts", async () => {
    const fetchMock = captureRequest();

    await createPost("clip-1", "", "private");

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/posts");
    expect((init as RequestInit).method).toBe("POST");
  });
});

describe("setClipVisibility", () => {
  // No UI calls this yet — narrowing a clip is a control this card did not
  // build. It ships with the endpoint rather than after it, and it is pinned
  // so it cannot rot into a helper that PATCHes the wrong shape the first time
  // something does call it.
  it("PATCHes the clip's own visibility", async () => {
    const fetchMock = captureRequest();

    await setClipVisibility("clip-1", "private");

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/clips/clip-1/visibility");
    expect((init as RequestInit).method).toBe("PATCH");
    expect(bodyOf(fetchMock)).toEqual({ visibility: "private" });
  });
});
