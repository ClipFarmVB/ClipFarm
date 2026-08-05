import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Game } from "./api";

vi.mock("./api", () => ({ getGames: vi.fn() }));

function game(id: string): Game {
  return {
    id,
    title: id,
    status: "ready",
    progress: 100,
    progress_stage: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

/**
 * The cache is module-level state, so every test needs its own copy of the
 * module rather than a reset helper the module doesn't expose.
 */
async function load() {
  vi.resetModules();
  const { getGames } = await import("./api");
  const cache = await import("./gamesCache");
  return { getGames: vi.mocked(getGames), cache };
}

/** A promise whose resolution this test controls, to hold a fetch in flight. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("fetchGames — stale-response protection (CF-63)", () => {
  it("does not let a fetch that started before a write clobber the newer cache", async () => {
    const { getGames, cache } = await load();
    const inflight = deferred<Game[]>();
    getGames.mockReturnValueOnce(inflight.promise);

    const pending = cache.fetchGames();

    // A deliberate write lands while the request is still out.
    cache.updateGamesCache([game("written-during-fetch")]);

    // The response is from before that write.
    inflight.resolve([game("stale")]);

    // Both the cache and what the caller renders must be the newer view.
    expect(await pending).toEqual([game("written-during-fetch")]);
    expect(cache.getCachedGames()).toEqual([game("written-during-fetch")]);
  });

  it("re-fetches rather than returning pre-write data when the cache is cold", async () => {
    const { getGames, cache } = await load();
    const inflight = deferred<Game[]>();
    getGames
      .mockReturnValueOnce(inflight.promise)
      .mockResolvedValueOnce([game("uploaded"), game("existing")]);

    const pending = cache.fetchGames();

    // An upload into a cold cache: bumps the generation, leaves _data null, so
    // there is no newer view to fall back on.
    cache.addGameToCache(game("uploaded"));
    inflight.resolve([game("existing")]);

    // Returning the pre-upload list here is the CF-63 symptom itself.
    expect(await pending).toEqual([game("uploaded"), game("existing")]);
    expect(getGames).toHaveBeenCalledTimes(2);
  });

  it("bounds the retries instead of looping while writes keep landing", async () => {
    const { getGames, cache } = await load();
    // Every response loses its race: the write happens while it is in flight
    // and leaves the cache cold, so the guard retries.
    getGames.mockImplementation(async () => {
      cache.addGameToCache(game("racing"));
      return [game("always-stale")];
    });

    const result = await cache.fetchGames();

    // One attempt plus the two retries, then it gives up and returns what it
    // has — a caller is never left waiting on a loop.
    expect(getGames).toHaveBeenCalledTimes(3);
    expect(result).toEqual([game("always-stale")]);
  });

  it("writes back normally when nothing raced it", async () => {
    const { getGames, cache } = await load();
    getGames.mockResolvedValueOnce([game("a")]);

    expect(await cache.fetchGames()).toEqual([game("a")]);
    expect(cache.getCachedGames()).toEqual([game("a")]);
    expect(getGames).toHaveBeenCalledTimes(1);
  });
});

describe("prefetchGames", () => {
  it("issues one request when called repeatedly", async () => {
    const { getGames, cache } = await load();
    const inflight = deferred<Game[]>();
    getGames.mockReturnValue(inflight.promise);

    cache.prefetchGames();
    cache.prefetchGames();
    cache.prefetchGames();

    expect(getGames).toHaveBeenCalledTimes(1);

    inflight.resolve([game("a")]);
    await cache.getInflightGames();
  });

  it("clears the in-flight promise once it settles", async () => {
    const { getGames, cache } = await load();
    getGames.mockResolvedValueOnce([game("a")]);

    cache.prefetchGames();
    expect(cache.getInflightGames()).not.toBeNull();

    await cache.getInflightGames();
    expect(cache.getInflightGames()).toBeNull();
  });

  it("clears the in-flight promise when the fetch rejects", async () => {
    const { getGames, cache } = await load();
    getGames.mockRejectedValueOnce(new Error("network"));

    cache.prefetchGames();
    await expect(cache.getInflightGames()).rejects.toThrow("network");
    expect(cache.getInflightGames()).toBeNull();
  });

  it("skips the request while the cache is fresh", async () => {
    const { getGames, cache } = await load();
    cache.updateGamesCache([game("a")]);

    cache.prefetchGames();

    expect(getGames).not.toHaveBeenCalled();
  });
});

describe("addGameToCache", () => {
  it("prepends the new game", async () => {
    const { cache } = await load();
    cache.updateGamesCache([game("older")]);

    cache.addGameToCache(game("newer"));

    expect(cache.getCachedGames()).toEqual([game("newer"), game("older")]);
  });

  it("does not duplicate a game already in the list", async () => {
    const { cache } = await load();
    cache.updateGamesCache([game("a"), game("b")]);

    cache.addGameToCache(game("b"));

    // Two rows with the same id would hand React duplicate keys.
    expect(cache.getCachedGames()).toEqual([game("b"), game("a")]);
  });

  it("leaves a cold cache cold", async () => {
    const { cache } = await load();

    cache.addGameToCache(game("uploaded"));

    expect(cache.getCachedGames()).toBeNull();
  });
});

describe("getCachedGames", () => {
  it("returns null once the entry is stale", async () => {
    vi.useFakeTimers();
    try {
      const { cache } = await load();
      cache.updateGamesCache([game("a")]);
      expect(cache.getCachedGames()).toEqual([game("a")]);

      vi.advanceTimersByTime(30_001);

      expect(cache.getCachedGames()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
