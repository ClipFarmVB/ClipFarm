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

function _publish(me: Me | null) {
  _me = me;
  for (const notify of _subscribers) notify(me);
}

/** Fetch once and share the result with every caller. */
export function fetchMe(): Promise<Me | null> {
  if (_me) return Promise.resolve(_me);
  if (!_promise) {
    _promise = getMe()
      .then((me) => {
        _publish(me);
        return me;
      })
      .catch(() => {
        // Signed out (401) or a transient failure. Null is a valid answer —
        // callers render the signed-out state rather than an error.
        _publish(null);
        return null;
      })
      .finally(() => {
        _promise = null;
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
