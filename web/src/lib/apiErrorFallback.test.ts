/**
 * CF-304: the fallback error string is now something a user reads.
 *
 * `throwApiError` builds `API error <status>: <body>` for any response
 * `apiErrorMessage` cannot make a sentence out of. While those strings reached
 * `console.error`, carrying the whole body was free. The error paths fixed in
 * CF-304 put them in an `alert()` and on the profile page, so a proxy's HTML
 * 502 became a wall of markup with the one useful token — the status — at the
 * far end of it.
 *
 * Exercised through `getProfile` rather than by exporting the helper: what
 * matters is the message a caller actually receives, and every hand-rolled
 * path in `api.ts` shares this one function.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getProfile } from "./api";

function failWith(status: number, body: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status,
      headers: new Headers(),
      json: async () => JSON.parse(body),
      text: async () => body,
    }),
  );
}

beforeEach(() => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the error message for a body the API did not write", () => {
  it("drops an HTML page entirely and keeps the status", async () => {
    const page = `<!DOCTYPE html><html><body>${"<p>gateway</p>".repeat(200)}</body></html>`;
    failWith(502, page);

    await expect(getProfile("alice")).rejects.toThrow("API error 502");
    await expect(getProfile("alice")).rejects.not.toThrow("gateway");
  });

  it("cuts a long non-JSON body rather than pasting all of it", async () => {
    failWith(500, "x".repeat(5000));

    const err = await getProfile("alice").catch((e: Error) => e);
    expect(err).toBeInstanceOf(Error);
    // Bounded, and visibly cut so nobody reads the truncation as the whole
    // answer. The exact cap is not the contract; "far shorter than the body" is.
    expect((err as Error).message.length).toBeLessThan(300);
    expect((err as Error).message).toContain("API error 500");
    expect((err as Error).message.endsWith("…")).toBe(true);
  });

  it("still passes a short non-JSON body through", async () => {
    // The other direction: a one-line body from a small proxy is the case the
    // fallback exists for, and truncation must not swallow it.
    failWith(503, "upstream connect error");

    await expect(getProfile("alice")).rejects.toThrow("API error 503: upstream connect error");
  });

  it("prefers the API's own sentence and never reaches the fallback", async () => {
    failWith(404, JSON.stringify({ detail: "Profile not found" }));

    const err = await getProfile("alice").catch((e: Error) => e);
    expect((err as Error).message).toBe("Profile not found");
  });
});
