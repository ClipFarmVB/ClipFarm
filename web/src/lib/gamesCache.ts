/**
 * Module-level games cache with stale-while-revalidate semantics.
 *
 * AuthContext calls prefetch() as soon as the session resolves so the
 * API fetch is already in-flight (or done) by the time the user lands
 * on /games.  The page reads getCached() for an instant render and
 * getInflight() to avoid firing a duplicate request.
 *
 * Every deliberate write (update / add) bumps a generation counter and drops
 * the in-flight promise.  A fetch that started before the write resolves
 * against a stale generation and is discarded instead of clobbering the cache
 * with pre-write data (CF-63).
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
 * happened while the request was in flight.  Callers can always render what
 * the promise resolves to — it is never a response we already know is stale.
 *
 * On a lost race we prefer the cache's newer view; with a cold cache there is
 * nothing newer to fall back on, so we re-fetch rather than hand back the
 * pre-mutation list — returning it would reproduce the very CF-63 symptom this
 * guard exists to prevent (an upload writing into a cold cache is exactly that
 * case).  Retries are bounded; a caller is never left waiting on a loop.
 */
const _MAX_RESTALE_RETRIES = 2;

export function fetchGames(): Promise<Game[]> {
  const attempt = (retriesLeft: number): Promise<Game[]> => {
    const gen = _generation;
    return getGames().then((games) => {
      if (gen === _generation) {
        _data = games;
        _fetchedAt = Date.now();
        return games;
      }
      if (_data) return _data;
      if (retriesLeft > 0) return attempt(retriesLeft - 1);
      return games;
    });
  };
  return attempt(_MAX_RESTALE_RETRIES);
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

/**
 * Drop the cache when the signed-in identity changes — sign-out, an expiry, or
 * a switch straight into another account. Game titles carry opponent names,
 * dates and locations, so the previous user's library surviving into the next
 * session is a disclosure, not a stale-render bug (CF-299).
 *
 * Bumps the generation for the reason every deliberate write does (CF-63): a
 * fetch already in flight would otherwise resolve into the cache afterwards.
 *
 * **Dropping `_promise` is the load-bearing half, and not for that reason.**
 * `prefetchGames()` opens with `if (_promise) return`, so leaving it behind
 * makes the next user's sign-in prefetch a no-op and leaves them waiting on a
 * chain started for the previous account. The generation bump alone would not
 * cover that, because the bump only decides who may *write*.
 *
 * The orphaned chain is marked handled before the reference goes. After this
 * nothing awaits it — `getInflightGames()` returns null and the `_promise === p`
 * guards in `prefetchGames` no longer match — so its rethrow would surface as
 * an unhandled rejection on every sign-out with a warm prefetch, which the
 * browser Sentry SDK reports. Routine 401 noise is how a real signal gets
 * buried.
 *
 * **This cannot defend against a fetch issued while the old token is still
 * valid.** `attempt()` re-reads the generation and re-requests, and that retry
 * is written to the cache; whether it carries the previous user's rows depends
 * only on whether the session was revoked first. That is why the call site is
 * after `signOut()` resolves rather than beside `clearMe()` — see AuthContext.
 */
export function clearGamesCache(): void {
  _generation++;
  if (_promise) _promise.catch(() => {});
  _promise = null;
  _data = null;
  _fetchedAt = 0;
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
 *
 * Drops any existing row for the same id first: a repeat call would otherwise
 * duplicate the game and hand React two children with the same key.
 *
 * Note this refreshes _fetchedAt off a *local* write, so the 30s freshness
 * window can extend without a real fetch — the list may lag server-side
 * changes by that long.  Harmless while a library has a single writer.
 */
export function addGameToCache(game: Game): void {
  _generation++;
  _promise = null;
  if (_data) {
    _data = [game, ..._data.filter((g) => g.id !== game.id)];
    _fetchedAt = Date.now();
  }
}
