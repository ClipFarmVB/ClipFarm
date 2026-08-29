/**
 * What the client needs from whichever app is hosting it.
 *
 * Both fields are injected rather than read from the environment, because the
 * two things this module used to reach for are the two that do not survive the
 * trip off the web: `process.env.NEXT_PUBLIC_API_URL` is a Next build-time
 * constant, and the bearer token came from `@supabase/ssr`, which is
 * browser-only. Under Expo the same values come from app config and
 * `expo-secure-store`. Neither host can see the other's, so the client knows
 * about neither and is handed both (CF-314).
 */
export interface ApiClientConfig {
  /**
   * Root of the api, without a trailing slash — e.g. `https://api.clipfarm.ca`.
   * Paths are appended verbatim, so a trailing slash produces a double one.
   */
  baseUrl: string;
  /**
   * The caller's bearer token, or `null` when signed out.
   *
   * Called before every request rather than read once at init, so a token that
   * refreshes mid-session is picked up without re-configuring. May be async —
   * `supabase.auth.getSession()` and `SecureStore.getItemAsync()` both are —
   * and may reject: a token that cannot be read sends the request out
   * unauthenticated rather than failing it, which is what the web client did
   * before this package existed.
   */
  getToken: () => string | null | Promise<string | null>;
}

let config: ApiClientConfig | null = null;

/**
 * Bind the client to its host app. Call once, before the first request —
 * `web/src/lib/api.ts` and the Expo app's equivalent are the two callers.
 */
export function configureApiClient(next: ApiClientConfig): void {
  config = next;
}

/**
 * The active config, or a loud failure if nothing configured it.
 *
 * Deliberately a throw and not a default `http://localhost:8000`: a mobile
 * build that forgot to initialise would otherwise spend its requests on a host
 * that does not exist and report them as network errors.
 */
export function getApiClientConfig(): ApiClientConfig {
  if (!config) {
    throw new Error(
      "@clipfarm/api-client is not configured — call configureApiClient({ baseUrl, getToken }) before the first request.",
    );
  }
  return config;
}
