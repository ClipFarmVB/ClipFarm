/**
 * CF-109: an absent body from the posts listing must read as "no posts".
 *
 * `request()` returns `undefined as T` for an empty body. That is deliberate
 * and right for the DELETEs it was added for — every one of them returns 204,
 * and it is what let `deleteGame` stop hand-rolling its own fetch. For a list
 * it is a lie the type system cannot catch: the caller is handed `undefined`
 * typed as `Post[]`, and the first `.length` throws a TypeError a long way
 * from here.
 *
 * `PostGrid` does not save it either. Its loading guard is `posts === null`,
 * and `undefined` is not `null`, so the component falls through to
 * `posts.length` and renders a blank page instead of the error card it has for
 * exactly this. Coerced in `getUserPosts`, where the shape is known, rather
 * than in the component that happens to be today's only caller.
 *
 * The 200-with-no-body case is the one worth pinning rather than the 204: a
 * proxy, a truncated response, or a misconfigured gateway all produce it, and
 * it is the arm of that condition nobody writes a test for.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getUserPosts } from "./api";

function respondWith(init: { status: number; body: string | null; length?: string }) {
  const headers = new Headers();
  if (init.length !== undefined) headers.set("content-length", init.length);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: init.status,
      headers,
      json: async () => {
        if (init.body === null) throw new SyntaxError("Unexpected end of JSON input");
        return JSON.parse(init.body);
      },
      text: async () => init.body ?? "",
    }),
  );
}

beforeEach(() => {
  // getAuthHeaders reaches Supabase; irrelevant to the response handling here.
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("getUserPosts", () => {
  it("returns an array for an empty 200, not undefined", async () => {
    respondWith({ status: 200, body: null, length: "0" });

    const posts = await getUserPosts("alice");

    // The assertion that matters is the second one: `toEqual([])` alone passes
    // for `undefined` under some matchers, and the bug is precisely that the
    // caller cannot call array methods on what it gets back.
    expect(Array.isArray(posts)).toBe(true);
    expect(posts.length).toBe(0);
  });

  it("returns an array for a 204", async () => {
    respondWith({ status: 204, body: null });

    const posts = await getUserPosts("alice");

    expect(Array.isArray(posts)).toBe(true);
  });

  it("still returns the posts when there are some", async () => {
    respondWith({
      status: 200,
      body: JSON.stringify([{ id: "p1" }, { id: "p2" }]),
    });

    const posts = await getUserPosts("alice");

    expect(posts.map((p) => p.id)).toEqual(["p1", "p2"]);
  });
});
