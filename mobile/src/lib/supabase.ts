// URL and URLSearchParams are incomplete in the React Native runtime, and
// supabase-js builds request URLs with them. Imported for the side effect, and
// first, so the polyfill is installed before the client is constructed.
import "react-native-url-polyfill/auto";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import * as SecureStore from "expo-secure-store";

import { SUPABASE_ANON_KEY, SUPABASE_URL } from "./env";
import { createChunkedSecureStorage } from "./secureStore";

let client: SupabaseClient | null = null;

/**
 * The app's Supabase client — the same project the web app signs into, so an
 * existing ClipFarm account works with no migration.
 *
 * Three settings differ from `web/src/lib/supabase.ts`, all of them because
 * this is not a browser:
 *
 * - `storage` is the keychain / Android keystore, not cookies. This is what
 *   makes the session survive a cold start.
 * - `detectSessionInUrl` is off: there is no URL bar to read a callback out of.
 *   Any OAuth flow arrives as a deep link and is handled explicitly (CF-326).
 * - `autoRefreshToken` is on, but the timer only counts while the app is
 *   foregrounded — see `startAutoRefreshOnForeground` in ./session.
 */
export function getSupabaseClient(): SupabaseClient {
  if (client) return client;
  client = createClient(SUPABASE_URL(), SUPABASE_ANON_KEY(), {
    auth: {
      storage: createChunkedSecureStorage(SecureStore),
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: false,
    },
  });
  return client;
}
