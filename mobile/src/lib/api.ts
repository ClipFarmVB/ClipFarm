/**
 * Binds the api client to this app, once, at import.
 *
 * `configureApiClient` is the stand-in in ./apiClient while CF-314 (#418) is in
 * flight; its configuration surface is that package's, so when #418 lands this
 * import becomes `@clipfarm/api-client` and ./apiClient is deleted. Screens
 * then call the package's endpoint functions — see ./apiClient for what does
 * and does not carry over.
 *
 * Imported for its side effect by `src/app/_layout.tsx`, which is the module
 * the app cannot start without loading. Binding here rather than inside a
 * screen means `API_URL()` is evaluated at startup, so a release build with no
 * `EXPO_PUBLIC_API_URL` fails immediately and by name instead of surviving to
 * the first request and reporting it as a network error. That is deliberate —
 * it is the same choice ./env makes for the Supabase values.
 */
import { configureApiClient } from "./apiClient";
import { API_URL } from "./env";
import { getAccessToken } from "./session";

configureApiClient({
  baseUrl: API_URL(),
  getToken: getAccessToken,
});

export { apiRequest } from "./apiClient";
