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

import { ApiError, getProfile, uploadAvatar } from "./api";

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

describe("the status travels with the error (CF-423)", () => {
  it("rejects a 404 with an ApiError carrying the status and the unchanged message", async () => {
    failWith(404, JSON.stringify({ detail: "Profile not found" }));

    const err = await getProfile("alice").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    // Still an Error, so every caller that only reads `.message` is unaffected.
    expect(err).toBeInstanceOf(Error);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("Profile not found");
    expect((err as ApiError).name).toBe("ApiError");
  });

  it("carries the status of a response the API did not write", async () => {
    // A proxy's HTML 502: the message is nothing but the status, and now the
    // status is also a number a caller can branch on.
    failWith(502, "<!DOCTYPE html><html><body>Bad gateway</body></html>");

    const err = await getProfile("alice").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).message).toBe("API error 502");
  });

  it("does the same on the hand-rolled avatar upload path", async () => {
    failWith(413, JSON.stringify({ detail: "Avatar must be 2 MB or smaller" }));

    const file = new File(["x"], "a.png", { type: "image/png" });
    const err = await uploadAvatar(file).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(413);
    expect((err as ApiError).message).toBe("Avatar must be 2 MB or smaller");
  });
});
