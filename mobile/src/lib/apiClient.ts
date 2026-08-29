/**
 * Local stand-in for `packages/api-client` (CF-314), which is in flight and not
 * on `main` yet.
 *
 * It is deliberately the *same shape* CF-314 is landing — a client initialised
 * with `{ baseUrl, getToken }`, where `getToken` may be async and returns null
 * when signed out — so adopting the real package is an import change in
 * `src/lib/api.ts` and the deletion of this file, not a rewrite of anything
 * that calls it.
 *
 * Only `request` lives here. The endpoint wrappers (`getGames`, `getClips`, …)
 * are CF-314's to move out of `web/src/lib/api.ts`; re-typing them here would
 * guarantee two versions that drift.
 */

export interface ApiClientOptions {
  baseUrl: string;
  /** The caller's access token, or null when signed out. */
  getToken: () => string | null | Promise<string | null>;
}

export interface ApiClient {
  request<T>(path: string, init?: RequestInit): Promise<T>;
}

/**
 * Prefer the server's own explanation of a failure.
 *
 * The upload limits (CF-91) write real, actionable sentences into `detail` —
 * "you have 60 min of your 360 min per 24 hours left" — which the raw body
 * buries inside JSON. `web/src/lib/apiError.ts` is the full version and moves
 * into the package in CF-314; this is the same rule, short enough not to be
 * worth reconciling later.
 */
function errorMessage(body: string, fallback: string): string {
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  } catch {
    // Not JSON — fall through to the status line.
  }
  return fallback;
}

export function createApiClient({ baseUrl, getToken }: ApiClientOptions): ApiClient {
  return {
    async request<T>(path: string, init?: RequestInit): Promise<T> {
      const token = await getToken();
      const res = await fetch(`${baseUrl}${path}`, {
        ...init,
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init?.headers,
        },
      });

      if (!res.ok) {
        const body = await res.text();
        throw new Error(errorMessage(body, `API error ${res.status}: ${body}`));
      }
      // Every DELETE in this API returns 204, and a 200 with no body happens
      // too. `res.json()` rejects on both with a parse error that reads as if
      // the response were malformed rather than absent.
      if (res.status === 204 || res.headers.get("content-length") === "0") {
        return undefined as T;
      }
      return (await res.json()) as T;
    },
  };
}
