/**
 * Module-level games cache with stale-while-revalidate semantics.
 *
 * AuthContext calls prefetch() as soon as the session resolves so the
 * API fetch is already in-flight (or done) by the time the user lands
 * on /games.  The page reads getCached() for an instant render and
 * getInflight() to avoid firing a duplicate request.
 *
 * Every deliberate write (update / add / invalidate) bumps a generation
 * counter and drops the in-flight promise.  A prefetch that started
 * before the write resolves against a stale generation and is discarded
 * instead of clobbering the cache with pre-write data (CF-63).
 */

import { getGames, type Game } from "./api";

const STALE_MS = 30_000; // cache is fresh for 30 seconds

let _data: Game[] | null = null;
let _fetchedAt = 0;
let _promise: Promise<Game[]> | null = null;
let _generation = 0;

/**
 * Fetch the games list with stale-response protection: the result is only
 * written back if no deliberate cache write (update / add / invalidate)
 * happened while the request was in flight.  On a lost race the promise
 * resolves with the cache's newer view instead of the stale response, so
 * callers can always render what it returns.
 */
export function fetchGames(): Promise<Game[]> {
  const gen = _generation;
  return getGames().then((games) => {
    if (gen === _generation) {
      _data = games;
      _fetchedAt = Date.now();
      return games;
    }
    return _data ?? games;
  });
}

/** Start a background fetch if the cache is cold or stale. No-op if already in-flight. */
export function prefetchGames(): void {
  if (_promise) return;
  if (_data && Date.now() - _fetchedAt < STALE_MS) return;
  const p = fetchGames()
    .then((games) => {
      if (_promise === p) _promise = null;
      return games;
    })
    .catch((err) => {
      if (_promise === p) _promise = null;
      throw err;
    });
  _promise = p;
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
  _generation++;
  _promise = null;
  _data = games;
  _fetchedAt = Date.now();
}

/**
 * Prepend a newly created game (the list is newest-first) so the Library
 * shows it immediately after upload.  If the cache is cold, leave it cold —
 * the next visit fetches fresh, and the generation bump guarantees a fetch
 * that predates the upload can't resolve into the cache without it.
 */
export function addGameToCache(game: Game): void {
  _generation++;
  _promise = null;
  if (_data) {
    _data = [game, ..._data];
    _fetchedAt = Date.now();
  }
}

/** Invalidate — the next visit fetches fresh. */
export function invalidateGamesCache(): void {
  _generation++;
  _promise = null;
  _data = null;
}
