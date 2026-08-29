import { describe, expect, it } from "vitest";

import { CHUNK_SIZE, createChunkedSecureStorage, splitIntoChunks } from "./secureStore";
import type { SecureStoreLike } from "./secureStore";

/** An in-memory stand-in for the native keychain, plus the one thing that
 *  matters about it: nothing may be written over the platform cap. */
function fakeStore() {
  const values = new Map<string, string>();
  const writes: string[] = [];
  const store: SecureStoreLike = {
    async getItemAsync(key) {
      return values.get(key) ?? null;
    },
    async setItemAsync(key, value) {
      writes.push(key);
      values.set(key, value);
    },
    async deleteItemAsync(key) {
      values.delete(key);
    },
  };
  return { store, values, writes };
}

const LIMIT_BYTES = 2048;

/** True when the string contains a surrogate with no partner — what a naive
 *  code-unit slice through an emoji leaves behind. */
function hasLoneSurrogate(value: string): boolean {
  return /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(
    value
  );
}

describe("createChunkedSecureStorage", () => {
  it("round-trips a value larger than the platform cap", async () => {
    const { store, values } = fakeStore();
    const storage = createChunkedSecureStorage(store);
    const session = JSON.stringify({ access_token: "a".repeat(4000), user: { id: "u1" } });

    await storage.setItem("sb-auth-token", session);

    expect(await storage.getItem("sb-auth-token")).toBe(session);
    for (const [key, value] of values) {
      expect(
        new TextEncoder().encode(value).length,
        `${key} exceeds the SecureStore limit`
      ).toBeLessThanOrEqual(LIMIT_BYTES);
    }
  });

  it("keeps every chunk under the cap for multi-byte characters", () => {
    // Three bytes per code unit is the worst realistic case — a CJK display
    // name in the user object. ASCII passing is not evidence this does.
    const chunks = splitIntoChunks("好".repeat(CHUNK_SIZE * 3));
    for (const chunk of chunks) {
      expect(new TextEncoder().encode(chunk).length).toBeLessThanOrEqual(LIMIT_BYTES);
    }
  });

  it("does not split a surrogate pair across chunks", () => {
    // An emoji straddling the boundary: sliced on code units, each half is a
    // lone surrogate that does not survive the native bridge.
    const chunks = splitIntoChunks("a".repeat(CHUNK_SIZE - 1) + "🏐".repeat(4));
    for (const chunk of chunks) {
      expect(hasLoneSurrogate(chunk)).toBe(false);
    }
    expect(chunks.join("")).toBe("a".repeat(CHUNK_SIZE - 1) + "🏐".repeat(4));
  });

  it("sweeps chunks left over from a longer previous value", async () => {
    const { store, values } = fakeStore();
    const storage = createChunkedSecureStorage(store);

    await storage.setItem("k", "x".repeat(CHUNK_SIZE * 4));
    await storage.setItem("k", "short");

    expect(await storage.getItem("k")).toBe("short");
    expect([...values.keys()].sort()).toEqual(["k", "k.0"]);
  });

  it("removes the count and every chunk", async () => {
    const { store, values } = fakeStore();
    const storage = createChunkedSecureStorage(store);

    await storage.setItem("k", "y".repeat(CHUNK_SIZE * 3));
    await storage.removeItem("k");

    expect(values.size).toBe(0);
    expect(await storage.getItem("k")).toBeNull();
  });

  it("reads a missing key as no session rather than throwing", async () => {
    const { store } = fakeStore();
    expect(await createChunkedSecureStorage(store).getItem("absent")).toBeNull();
  });

  it("reads a half-written value as no session", async () => {
    const { store, values } = fakeStore();
    const storage = createChunkedSecureStorage(store);

    await storage.setItem("k", "z".repeat(CHUNK_SIZE * 3));
    values.delete("k.1");

    // Signed out is recoverable; a rejected promise during restore is not.
    expect(await storage.getItem("k")).toBeNull();
  });

  it("round-trips the empty string", async () => {
    const { store } = fakeStore();
    const storage = createChunkedSecureStorage(store);

    await storage.setItem("k", "");

    expect(await storage.getItem("k")).toBe("");
  });
});
