/**
 * A `SecureStore`-backed storage adapter for the Supabase session (CF-315).
 *
 * The app keeps its session in the keychain / Android keystore rather than in
 * cookies, which is what makes it survive a cold start. One catch makes this
 * more than a two-line adapter: **SecureStore values are capped at 2048 bytes
 * on Android**, and a Supabase session — two JWTs plus the user object — is
 * routinely larger. Over the cap, `setItemAsync` warns and the write is
 * unreliable, so the session comes back empty on the next launch and the user
 * is silently signed out. Hence chunking.
 *
 * Layout: the key itself holds a pointer — `generation:count:highWater` — and
 * the chunks live at `${key}.${generation}.${i}`. Uniform for every value, so
 * there is no "small values are stored differently" branch to get wrong.
 *
 * The generation is what makes a write survive being interrupted. It alternates
 * 0, 1, 0, … so a new value is written into the generation that is *not* live,
 * never over the chunks the current pointer refers to, and the single pointer
 * write is the commit. Killed anywhere before it — the keychain is locked, the
 * OS reclaims the app — the old value is still whole; killed after, the new one
 * is. Overwriting in place instead would leave the live count pointing at a
 * mix of both, which reads back as a spliced string that no longer parses and
 * that `getItem` has no way to recognise as damage.
 *
 * Written against an injected store rather than importing `expo-secure-store`
 * directly so the chunking is testable off-device — the native module is the
 * one thing a unit test cannot have.
 */

export interface SecureStoreLike {
  getItemAsync(key: string): Promise<string | null>;
  setItemAsync(key: string, value: string): Promise<void>;
  deleteItemAsync(key: string): Promise<void>;
}

/** What `createClient({ auth: { storage } })` wants. */
export interface SupabaseStorage {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  removeItem(key: string): Promise<void>;
}

/**
 * Chunk length in UTF-16 code units.
 *
 * The 2048-byte cap is on *bytes*, and the value is stored as UTF-8, where one
 * code unit can cost three (a CJK display name in the user object is the
 * realistic case). 640 × 3 = 1920 keeps the worst case under the cap instead of
 * only the ASCII case, which is the one that passes in development and fails
 * for a user with a non-Latin name.
 */
export const CHUNK_SIZE = 640;

/**
 * Slice without splitting a surrogate pair.
 *
 * `String.prototype.slice` cuts on code units, so a boundary can land between
 * the halves of an astral character (an emoji in a display name). Each half is
 * a lone surrogate, which does not survive a round trip through the native
 * bridge as itself — it comes back as U+FFFD and the rejoined JSON no longer
 * parses. Nudging the boundary one unit earlier keeps every chunk well-formed.
 */
function chunkEnd(value: string, start: number): number {
  const end = Math.min(start + CHUNK_SIZE, value.length);
  if (end >= value.length) return end;
  const code = value.charCodeAt(end - 1);
  const isHighSurrogate = code >= 0xd800 && code <= 0xdbff;
  return isHighSurrogate ? end - 1 : end;
}

export function splitIntoChunks(value: string): string[] {
  if (value.length === 0) return [""];
  const chunks: string[] = [];
  let start = 0;
  while (start < value.length) {
    const end = chunkEnd(value, start);
    chunks.push(value.slice(start, end));
    start = end;
  }
  return chunks;
}

export function createChunkedSecureStorage(store: SecureStoreLike): SupabaseStorage {
  const chunkKey = (key: string, generation: number, index: number) =>
    `${key}.${generation}.${index}`;

  interface Pointer {
    generation: number;
    count: number;
    /** Highest chunk index ever written under this key, plus one. */
    highWater: number;
  }

  /**
   * The pointer, or null for "no value" — which is also what an unparseable
   * pointer means. Anything this cannot read is a value that cannot be
   * returned, and saying so is how the caller stays on a path it can recover
   * from.
   */
  async function readPointer(key: string): Promise<Pointer | null> {
    const raw = await store.getItemAsync(key);
    if (raw === null) return null;
    const parts = raw.split(":");
    if (parts.length !== 3) return null;
    const [generation, count, highWater] = parts.map((part) => Number.parseInt(part, 10));
    const valid =
      (generation === 0 || generation === 1) &&
      Number.isInteger(count) &&
      count >= 0 &&
      Number.isInteger(highWater) &&
      highWater >= count;
    return valid ? { generation, count, highWater } : null;
  }

  async function deleteChunks(
    key: string,
    generation: number,
    from: number,
    to: number
  ): Promise<void> {
    for (let i = from; i < to; i++) {
      await store.deleteItemAsync(chunkKey(key, generation, i));
    }
  }

  return {
    async getItem(key) {
      const pointer = await readPointer(key);
      if (pointer === null) return null;

      const parts: string[] = [];
      for (let i = 0; i < pointer.count; i++) {
        const part = await store.getItemAsync(chunkKey(key, pointer.generation, i));
        // A missing chunk means a half-wiped value. Return null — Supabase
        // reads that as "no session" and sends the user to sign in, which is
        // recoverable. Throwing here would instead surface as an unhandled
        // rejection during session restore, on launch, with no way out but
        // reinstalling.
        if (part === null) return null;
        parts.push(part);
      }
      return parts.join("");
    },

    async setItem(key, value) {
      const live = await readPointer(key);
      // The generation that is not live. With no live pointer either is free.
      const generation = live?.generation === 0 ? 1 : 0;
      const chunks = splitIntoChunks(value);
      const highWater = Math.max(live?.highWater ?? 0, chunks.length);

      for (let i = 0; i < chunks.length; i++) {
        await store.setItemAsync(chunkKey(key, generation, i), chunks[i]);
      }
      // The commit. Every chunk it names is already stored, and none of them
      // was written over anything the previous pointer named.
      await store.setItemAsync(key, `${generation}:${chunks.length}:${highWater}`);

      // Housekeeping, past the point where an interruption can cost anything:
      // the generation just retired, and any chunk of this one left over from a
      // longer value. `highWater` bounds both — a deleted key that was never
      // there is free, an orphaned chunk of a session token is not.
      if (live) await deleteChunks(key, live.generation, 0, live.highWater);
      await deleteChunks(key, generation, chunks.length, highWater);
    },

    async removeItem(key) {
      const pointer = await readPointer(key);
      // Pointer first: from here on the value is gone as far as any reader is
      // concerned, whether or not the sweep below finishes.
      await store.deleteItemAsync(key);
      if (pointer === null) return;
      await deleteChunks(key, 0, 0, pointer.highWater);
      await deleteChunks(key, 1, 0, pointer.highWater);
    },
  };
}
