/**
 * Module-level games cache with stale-while-revalidate semantics.
 *
 * AuthContext calls prefetch() as soon as the session resolves so the
 * API fetch is already in-flight (or done) by the time the user lands
 * on /games.  The page reads getCached() for an instant render and
 * getInflight() to avoid firing a duplicate request.
 */

import { getGames, type Game } from "./api";

const STALE_MS = 30_000; // cache is fresh for 30 seconds

let _data: Game[] | null = null;
let _fetchedAt = 0;
let _promise: Promise<Game[]> | null = null;

/** Start a background fetch if the cache is cold or stale. No-op if already in-flight. */
export function prefetchGames(): void {
  if (_promise) return;
  if (_data && Date.now() - _fetchedAt < STALE_MS) return;
  _promise = getGames()
    .then((games) => {
      _data = games;
      _fetchedAt = Date.now();
      _promise = null;
      return games;
    })
    .catch((err) => {
      _promise = null;
      throw err;
    });
}

/** Returns cached data if still fresh, otherwise null. */
export function getCachedGames(): Game[] | null {
  if (_data && Date.now() - _fetchedAt < STALE_MS) return _data;
  return null;
}

/** Returns the in-flight promise so callers can await it without issuing a duplicate request. */
export function getInflightGames(): Promise<Game[]> | null {
  return _promise;
}

/** Write-through update — call after any mutation that changes the list. */
export function updateGamesCache(games: Game[]): void {
  _data = games;
  _fetchedAt = Date.now();
}

/** Invalidate — call after upload so the next visit fetches fresh. */
export function invalidateGamesCache(): void {
  _data = null;
  _promise = null;
}
