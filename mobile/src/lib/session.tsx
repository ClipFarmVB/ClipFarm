/**
 * Session state for the whole app (CF-315).
 *
 * Two jobs: restore the stored session on launch before anything routes, and
 * expose the current access token to the API client. Sign-in and sign-up UI is
 * CF-328's — this only provides the calls.
 */
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AppState } from "react-native";
import type { Session, User } from "@supabase/supabase-js";

import { getSupabaseClient } from "./supabase";

export interface SessionValue {
  session: Session | null;
  user: User | null;
  /**
   * True until the stored session has been read back. Routing waits on this:
   * acting before it resolves sends a signed-in user to the sign-in screen for
   * a frame on every cold start.
   */
  isRestoring: boolean;
  signIn(email: string, password: string): Promise<void>;
  signUp(email: string, password: string): Promise<void>;
  signOut(): Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

/**
 * Refresh tokens only while the app is in front.
 *
 * supabase-js runs the refresh on a timer, and a timer in a backgrounded React
 * Native app is suspended by the OS rather than fired late — so the library
 * ends up refreshing against a token that expired hours ago. Stopping on
 * background and restarting on foreground is what the RN setup expects; the
 * restart triggers an immediate refresh, which is exactly what a returning user
 * needs.
 */
function startAutoRefreshOnForeground(): () => void {
  const client = getSupabaseClient();
  const apply = (state: string) => {
    if (state === "active") {
      void client.auth.startAutoRefresh();
    } else {
      void client.auth.stopAutoRefresh();
    }
  };
  apply(AppState.currentState);
  const subscription = AppState.addEventListener("change", apply);
  return () => subscription.remove();
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [isRestoring, setIsRestoring] = useState(true);

  useEffect(() => {
    const client = getSupabaseClient();
    let active = true;

    // getSession() reads the keychain, so this is the cold-start restore.
    client.auth
      .getSession()
      .then(({ data }) => {
        if (active) setSession(data.session);
      })
      .catch(() => {
        // A session that cannot be read is a session the user does not have.
        // Signed out is recoverable; a rejected promise on launch is a blank
        // screen with no way forward.
        if (active) setSession(null);
      })
      .finally(() => {
        if (active) setIsRestoring(false);
      });

    const { data: listener } = client.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });
    const stopAutoRefresh = startAutoRefreshOnForeground();

    return () => {
      active = false;
      listener.subscription.unsubscribe();
      stopAutoRefresh();
    };
  }, []);

  const value = useMemo<SessionValue>(
    () => ({
      session,
      user: session?.user ?? null,
      isRestoring,
      async signIn(email, password) {
        const { error } = await getSupabaseClient().auth.signInWithPassword({
          email,
          password,
        });
        // Raw for now. CF-328 renders these through the shared wording in
        // `authError.ts`, which CF-314 moves into packages/api-client.
        if (error) throw error;
      },
      async signUp(email, password) {
        const { error } = await getSupabaseClient().auth.signUp({ email, password });
        if (error) throw error;
      },
      async signOut() {
        // CF-343 additionally unregisters the push token here, before the
        // token that authorises the call is thrown away.
        const { error } = await getSupabaseClient().auth.signOut();
        if (error) throw error;
      },
    }),
    [session, isRestoring]
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>");
  return value;
}

/**
 * The access token, for the API client.
 *
 * Reads through supabase-js rather than closing over React state: a request
 * fired seconds after a refresh must carry the new token, and `getSession()`
 * also refreshes an expired one on the way out.
 */
export async function getAccessToken(): Promise<string | null> {
  try {
    const { data } = await getSupabaseClient().auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}

// Re-exported so screens can type against the session without every one of them
// depending on @supabase/supabase-js directly.
export type { Session, User };
