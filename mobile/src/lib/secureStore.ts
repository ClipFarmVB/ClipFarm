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
 * Layout: the key itself holds the chunk count as a decimal string, and the
 * chunks live at `${key}.0`, `${key}.1`, … Uniform for every value, so there is
 * no "small values are stored differently" branch to get wrong.
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
  const chunkKey = (key: string, index: number) => `${key}.${index}`;

  async function readCount(key: string): Promise<number | null> {
    const raw = await store.getItemAsync(key);
    if (raw === null) return null;
    const count = Number.parseInt(raw, 10);
    return Number.isInteger(count) && count >= 0 ? count : null;
  }

  async function deleteChunks(key: string, from: number, to: number): Promise<void> {
    for (let i = from; i < to; i++) {
      await store.deleteItemAsync(chunkKey(key, i));
    }
  }

  return {
    async getItem(key) {
      const count = await readCount(key);
      if (count === null) return null;

      const parts: string[] = [];
      for (let i = 0; i < count; i++) {
        const part = await store.getItemAsync(chunkKey(key, i));
        // A missing chunk means a half-written or half-wiped value. Return null
        // — Supabase reads that as "no session" and sends the user to sign in,
        // which is recoverable. Throwing here would instead surface as an
        // unhandled rejection during session restore, on launch, with no way
        // out but reinstalling.
        if (part === null) return null;
        parts.push(part);
      }
      return parts.join("");
    },

    async setItem(key, value) {
      const previous = (await readCount(key)) ?? 0;
      const chunks = splitIntoChunks(value);

      for (let i = 0; i < chunks.length; i++) {
        await store.setItemAsync(chunkKey(key, i), chunks[i]);
      }
      // The count is written after the chunks it counts and before the stale
      // ones are swept: interrupted anywhere, the key either still names the
      // old complete value or already names the new one. Never a count
      // pointing at chunks that were never written.
      await store.setItemAsync(key, String(chunks.length));
      await deleteChunks(key, chunks.length, previous);
    },

    async removeItem(key) {
      const count = (await readCount(key)) ?? 0;
      await store.deleteItemAsync(key);
      await deleteChunks(key, 0, count);
    },
  };
}
