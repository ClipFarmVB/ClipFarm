/**
 * Shared access to the signed-in user's profile (CF-107).
 *
 * The sidebar, the claim banner and the profile pages all need `/users/me`.
 * Without sharing, that's three identical requests on every navigation, so a
 * module-level cache holds the result and de-duplicates concurrent callers —
 * same approach as gamesCache, kept smaller because a profile is one small
 * object with no stale-write hazard.
 *
 * `setMe` lets a component that just mutated the profile (claim, rename, avatar
 * upload) push the fresh copy in, so the sidebar updates without a refetch.
 */
import { useEffect, useState } from "react";

import { getMe, type Me } from "./api";

let _me: Me | null = null;
let _promise: Promise<Me | null> | null = null;
const _subscribers = new Set<(me: Me | null) => void>();

// Bumped by clearMe(). A fetch started before the bump belongs to the previous
// session and must not publish: nulling `_promise` drops our reference to the
// request but cannot cancel it, so its `.then` would otherwise write the
// signed-out user's profile back into the cache and every subscriber — the
// stale chrome that calling clearMe() on sign-out exists to prevent.
let _generation = 0;

function _publish(me: Me | null) {
  _me = me;
  for (const notify of _subscribers) notify(me);
}

/** Fetch once and share the result with every caller. */
export function fetchMe(): Promise<Me | null> {
  if (_me) return Promise.resolve(_me);
  if (!_promise) {
    const generation = _generation;
    const isCurrent = () => generation === _generation;

    _promise = getMe()
      .then((me) => {
        if (!isCurrent()) return null;
        _publish(me);
        return me;
      })
      .catch(() => {
        // Signed out (401) or a transient failure. Null is a valid answer —
        // callers render the signed-out state rather than an error.
        if (isCurrent()) _publish(null);
        return null;
      })
      .finally(() => {
        // Only clear the slot if it's still ours; a newer fetch may have
        // replaced it after clearMe().
        if (isCurrent()) _promise = null;
      });
  }
  return _promise;
}

/** Push a freshly-updated profile to every consumer. */
export function setMe(me: Me | null) {
  _publish(me);
}

/** Drop the cache — call on sign-out so the next user doesn't see stale data. */
export function clearMe() {
  _generation++;
  _me = null;
  _promise = null;
  _publish(null);
}

/**
 * Subscribe to the cached profile. `enabled` is false while signed out or while
 * the session is still resolving, which avoids a guaranteed-401 request.
 */
export function useMe(enabled: boolean): Me | null {
  const [me, setLocal] = useState<Me | null>(_me);

  useEffect(() => {
    if (!enabled) return;
    _subscribers.add(setLocal);
    void fetchMe();
    return () => {
      _subscribers.delete(setLocal);
    };
  }, [enabled]);

  return enabled ? me : null;
}
