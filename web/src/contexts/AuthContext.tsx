"use client";

import { createContext, useContext, useEffect, useState, useCallback, useRef, type ReactNode } from "react";
import { type Session, type User } from "@supabase/supabase-js";
import { createClient } from "@/lib/supabase";
import { clearGamesCache, prefetchGames } from "@/lib/gamesCache";

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
      const userId = session?.user.id ?? null;
      if (userId !== lastUserIdRef.current) {
        // Identity changed. `null` means we have not held one yet — a fresh
        // load, where module state is already empty and there is nothing to
        // drop; clearing there would be harmless but says something untrue.
        if (lastUserIdRef.current !== null) clearGamesCache();
        lastUserIdRef.current = userId;
      }
      setSession(session);
      setLoading(false);
      // Order is load-bearing: prefetchGames() returns early while a promise
      // from the previous identity is still in flight, so a clear after it
      // would leave this user with no fetch at all.
      if (session) prefetchGames();
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
