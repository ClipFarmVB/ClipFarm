import { describe, expect, it } from "vitest";

import { CHUNK_SIZE, createChunkedSecureStorage, splitIntoChunks } from "./secureStore";
import type { SecureStoreLike } from "./secureStore";

/** An in-memory stand-in for the native keychain, plus the one thing that
 *  matters about it: nothing may be written over the platform cap. */
function fakeStore() {
  const values = new Map<string, string>();
  const writes: string[] = [];
  /** Writes to allow before failing, as a locked keychain or an OS kill would. */
  let failAfter = Infinity;
  const store: SecureStoreLike = {
    async getItemAsync(key) {
      return values.get(key) ?? null;
    },
    async setItemAsync(key, value) {
      if (writes.length >= failAfter) throw new Error("keychain unavailable");
      writes.push(key);
      values.set(key, value);
    },
    async deleteItemAsync(key) {
      values.delete(key);
    },
  };
  return {
    store,
    values,
    writes,
    failAfterWrites(n: number) {
      failAfter = writes.length + n;
    },
  };
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
    // The pointer plus exactly one chunk: no orphaned fragment of the old
    // session token is left behind in the keychain.
    expect(values.size).toBe(2);
  });

  it("keeps the previous value when a write is interrupted", async () => {
    const { store, failAfterWrites } = fakeStore();
    const storage = createChunkedSecureStorage(store);
    const before = JSON.stringify({ access_token: "a".repeat(2000), v: 1 });
    const after = JSON.stringify({ access_token: "b".repeat(2000), v: 2 });

    await storage.setItem("k", before);
    // Two chunks in, phone locked. The old value must survive whole — a
    // half-overwritten one reads back as a splice of both and no longer parses.
    failAfterWrites(2);
    await expect(storage.setItem("k", after)).rejects.toThrow();

    expect(await storage.getItem("k")).toBe(before);
  });

  it("keeps the previous value when the commit itself fails", async () => {
    const { store, failAfterWrites } = fakeStore();
    const storage = createChunkedSecureStorage(store);
    const before = "old".repeat(CHUNK_SIZE);
    const after = "new".repeat(CHUNK_SIZE);

    await storage.setItem("k", before);
    // Every chunk of the new value lands and the pointer write is what dies:
    // the last moment at which the two values could still be confused.
    const chunkCount = splitIntoChunks(after).length;
    failAfterWrites(chunkCount);
    await expect(storage.setItem("k", after)).rejects.toThrow();

    expect(await storage.getItem("k")).toBe(before);
  });

  it("alternates generations so a write never lands on live chunks", async () => {
    const { store, writes } = fakeStore();
    const storage = createChunkedSecureStorage(store);

    await storage.setItem("k", "one");
    const first = writes.filter((key) => key !== "k");
    writes.length = 0;
    await storage.setItem("k", "two");
    const second = writes.filter((key) => key !== "k");

    expect(second).not.toEqual(first);
    expect(first.some((key) => second.includes(key))).toBe(false);
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

  it("reads a half-wiped value as no session", async () => {
    const { store, values } = fakeStore();
    const storage = createChunkedSecureStorage(store);

    await storage.setItem("k", "z".repeat(CHUNK_SIZE * 3));
    const chunk = [...values.keys()].find((key) => key.endsWith(".1"));
    values.delete(chunk!);

    // Signed out is recoverable; a rejected promise during restore is not.
    expect(await storage.getItem("k")).toBeNull();
  });

  it("reads an unparseable pointer as no session", async () => {
    const { store, values } = fakeStore();
    const storage = createChunkedSecureStorage(store);

    await storage.setItem("k", "value");
    values.set("k", "not-a-pointer");

    expect(await storage.getItem("k")).toBeNull();
  });

  it("round-trips the empty string", async () => {
    const { store } = fakeStore();
    const storage = createChunkedSecureStorage(store);

    await storage.setItem("k", "");

    expect(await storage.getItem("k")).toBe("");
  });
});
