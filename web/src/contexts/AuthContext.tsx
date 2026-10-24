"use client";

import { createContext, useContext, useEffect, useState, useCallback, useRef, type ReactNode } from "react";
import { type Session, type User } from "@supabase/supabase-js";
import { createClient } from "@/lib/supabase";
import { clearGamesCache, prefetchGames } from "@/lib/gamesCache";
import { clearMe, fetchMe } from "@/lib/useMe";

interface AuthState {
  user: User | null;
  session: Session | null;
  loading: boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  session: null,
  loading: true,
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  // Use a ref so the client is created once and never changes reference between renders
  const supabaseRef = useRef(createClient());
  const supabase = supabaseRef.current;

  // Whose games are in the module cache. Compared rather than the event name
  // so every way the identity can change is covered by one branch (CF-299).
  const lastUserIdRef = useRef<string | null>(null);
  // Whether any session has resolved yet. Distinct from `lastUserIdRef` being
  // null, which cannot tell "this page has never resolved a session" from
  // "this page is signed out" — and those need opposite handling below.
  const resolvedOnceRef = useRef(false);

  useEffect(() => {
    // Both entry points go through here so the ref's first assignment is
    // correct whichever resolves first.
    const onSession = (session: Session | null) => {
      // `?.user?.id`, not `?.user.id`: a session whose user failed to
      // deserialise would throw here, before `setLoading(false)` below, and
      // strand the whole app on the loading screen.
      const userId = session?.user?.id ?? null;
      if (!resolvedOnceRef.current) {
        // First resolution on this page. Any `getMe()` already in flight was
        // issued microseconds ago by a consumer's own effect, under this same
        // session, so it is the fetch we want rather than one to invalidate.
        resolvedOnceRef.current = true;
        lastUserIdRef.current = userId;
      } else if (userId !== lastUserIdRef.current) {
        // A change *after* the page has settled, which includes signing in
        // from a tab that was signed out. That case has no previous id, and
        // the guard this replaced skipped it for that reason — wrongly.
        // Consumers pass a constant `enabled` (PostGrid and ClipModal pass
        // SOCIAL_ENABLED), so such a tab already has an anonymous `getMe()`
        // in flight; without dropping it, `fetchMe()` below dedupes onto that
        // request, it 401s, and its catch publishes null over the user who
        // just signed in. The refetch then silently does nothing on the one
        // path it exists for.
        clearGamesCache();
        clearMe();
        lastUserIdRef.current = userId;
        // After the clears, so it cannot dedupe onto what they invalidated.
        if (userId !== null) void fetchMe();
      }
      setSession(session);
      setLoading(false);
      // Order is load-bearing: prefetchGames() returns early while a promise
      // from the previous identity is still in flight, so a clear after it
      // would leave this user with no fetch at all.
      // Keyed on the id rather than the session object: a session whose user
      // failed to deserialise is still truthy, and prefetching for it issues a
      // request on behalf of nobody.
      if (userId !== null) prefetchGames();
    };

    // Get initial session — kick off the games prefetch immediately so the
    // library page has data ready before the user even navigates there.
    supabase.auth.getSession().then(({ data }: { data: { session: Session | null } }) => {
      onSession(data.session);
    });

    // Listen for auth changes (sign-in, token refresh, sign-out).
    //
    // Keyed on the user id rather than on `_event`, which is why the event is
    // still ignored. A null session is not the only identity change: `/login`
    // is reachable while signed in (middleware guards only the protected
    // prefixes, and the page has no already-signed-in redirect), so signing
    // into a second account emits SIGNED_IN with a live session and never a
    // null one. An event- or null-gated clear misses that path entirely, and
    // the next prefetch no-ops against the previous user's still-fresh cache.
    // The id comparison also means TOKEN_REFRESHED and USER_UPDATED, which
    // carry the same id, cannot throw away a warm cache.
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event: string, session: Session | null) => {
        onSession(session);
      }
    );

    return () => subscription.unsubscribe();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    setSession(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user: session?.user ?? null,
        session,
        loading,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
