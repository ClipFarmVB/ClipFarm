/**
 * Binds the api client to this app, once, at import.
 *
 * `configureApiClient` is the stand-in in ./apiClient while CF-314 (#418) is in
 * flight; its configuration surface is that package's, so when #418 lands this
 * import becomes `@clipfarm/api-client` and ./apiClient is deleted. Screens
 * then call the package's endpoint functions — see ./apiClient for what does
 * and does not carry over.
 */
import { configureApiClient } from "./apiClient";
import { API_URL } from "./env";
import { getAccessToken } from "./session";

configureApiClient({
  baseUrl: API_URL(),
  getToken: getAccessToken,
});

export { apiRequest } from "./apiClient";
