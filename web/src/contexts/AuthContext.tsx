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

  useEffect(() => {
    // Both entry points go through here so the ref's first assignment is
    // correct whichever resolves first.
    const onSession = (session: Session | null) => {
      // `?.user?.id`, not `?.user.id`: a session whose user failed to
      // deserialise would throw here, before `setLoading(false)` below, and
      // strand the whole app on the loading screen.
      const userId = session?.user?.id ?? null;
      if (userId !== lastUserIdRef.current) {
        // Identity changed. A null previous id means this provider has not
        // seen one yet — the first resolution after a mount — so there is
        // nothing it is responsible for dropping.
        if (lastUserIdRef.current !== null) {
          // Both module caches, together. Clearing one and not the other is
          // the same disclosure with a different noun: `clearMe` is otherwise
          // reachable only from the sign-out button, so on the account-switch
          // path below — no sign-out, no null session — `fetchMe()` returns
          // the previous user's profile from `if (_me) return`, and B gets A's
          // handle and avatar in the chrome, A's `needsHandle` answer, and A's
          // id as the viewer.
          clearGamesCache();
          clearMe();
          // Clearing alone leaves the incoming user blank forever. `useMe`'s
          // effect is keyed on `enabled`, which does not change on the switch
          // path — the session goes A -> B with no null between and `loading`
          // is already false — so nothing re-subscribes and nothing refetches.
          // Worse than cosmetic: `needsHandle(null)` is false, so a genuinely
          // new user is never prompted to choose a handle, which is one of the
          // harms clearing was added to prevent, reached from the other side.
          if (userId !== null) void fetchMe();
        }
        lastUserIdRef.current = userId;
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
