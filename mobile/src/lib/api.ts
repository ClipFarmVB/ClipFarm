/**
 * The app's API client.
 *
 * `createApiClient` is a local stand-in while CF-314 is in flight — see
 * ./apiClient. When `packages/api-client` lands, this import becomes
 * `@clipfarm/api-client` and ./apiClient is deleted; nothing else changes,
 * because the injected shape is the same.
 */
import { createApiClient } from "./apiClient";
import { API_URL } from "./env";
import { getAccessToken } from "./session";

export const api = createApiClient({
  baseUrl: API_URL,
  getToken: getAccessToken,
});
