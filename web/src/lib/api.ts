/**
 * The web half of the shared api client (CF-314).
 *
 * The client itself lives in `packages/api-client` and is platform-agnostic:
 * it is handed a base URL and a way to read a bearer token, because the two
 * things it used to reach for — `process.env.NEXT_PUBLIC_API_URL` and the
 * `@supabase/ssr` browser session — do not exist under Expo. This module
 * supplies both for Next, then re-exports the client so every existing
 * `@/lib/api` import keeps working.
 *
 * Import the client through here, not from `@clipfarm/api-client` directly:
 * configuration happens when this module is first evaluated, and a caller that
 * bypasses it can reach a request before anything has been bound. That is why
 * `gamesCache`, `eta` and the auth/api error helpers are re-exported from here
 * too, rather than left as separate `@/lib/*` modules.
 */
import { configureApiClient } from "@clipfarm/api-client";
import { createClient } from "@/lib/supabase";

configureApiClient({
  baseUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  // Read per request rather than once, so a session that refreshes while the
  // tab is open keeps signing requests. A throw here is caught by the client
  // and sends the request out unauthenticated, which is what this code did
  // when it lived inside the client as `getAuthHeaders`.
  getToken: async () => {
    const supabase = createClient();
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  },
});

export * from "@clipfarm/api-client";
