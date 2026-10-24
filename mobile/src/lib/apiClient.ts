/**
 * Local stand-in for `packages/api-client` (CF-314, #418), which is in flight
 * and not on `main` yet.
 *
 * It mirrors that package's **configuration surface** exactly, because that is
 * the part this ticket has to get right: `configureApiClient({ baseUrl,
 * getToken })` as a module-level setter returning void, with `getToken` called
 * before every request rather than read once, allowed to be async, returning
 * null when signed out, and — when it rejects — sending the request out
 * unauthenticated rather than failing it.
 *
 * It does **not** mirror the rest. The real package exports named endpoint
 * functions (`getGames`, `createUpload`, …) and keeps `request` private; those
 * are CF-314's to move out of `web/src/lib/api.ts`, and re-typing them here
 * would guarantee two copies that drift. So `apiRequest` below is the one
 * interim symbol: it exists for a screen that needs the api before #418 lands,
 * and it is the one call site that has to change afterwards, to the endpoint
 * function for whatever it was fetching. Everything else is a swap of this
 * import for `@clipfarm/api-client`.
 */

export interface ApiClientConfig {
  /** Root of the api, without a trailing slash. Paths are appended verbatim. */
  baseUrl: string;
  /** The caller's bearer token, or null when signed out. May be async. */
  getToken: () => string | null | Promise<string | null>;
}

let config: ApiClientConfig | null = null;

/** Bind the client to its host app. Call once, before the first request. */
export function configureApiClient(next: ApiClientConfig): void {
  config = next;
}

/**
 * The active config, or a loud failure if nothing configured it.
 *
 * A throw rather than a default `http://localhost:8000`: a build that forgot to
 * initialise would otherwise spend its requests on a host that does not exist
 * and report them as network errors.
 */
export function getApiClientConfig(): ApiClientConfig {
  if (!config) {
    throw new Error(
      "The api client is not configured — call configureApiClient({ baseUrl, getToken }) before the first request."
    );
  }
  return config;
}

/**
 * Prefer the server's own explanation of a failure.
 *
 * The upload limits (CF-91) write real, actionable sentences into `detail` —
 * "you have 60 min of your 360 min per 24 hours left" — which the raw body
 * buries inside JSON. This mirrors `web/src/lib/apiError.ts`, which CF-314
 * moves into the package; both shapes `detail` takes are handled, because
 * web's comment records the array branch being missing once already and a
 * validation error showing raw JSON is the regression that caused.
 */
export function errorMessage(body: string, fallback: string): string {
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    // A 422 puts an array of {loc, msg, type} here. `msg` is the part a user
    // can act on ("String should have at most 500 characters"); `loc` and
    // `type` are for us and stay out.
    if (Array.isArray(detail)) {
      const messages = detail
        .map((entry) =>
          entry && typeof entry === "object" ? (entry as { msg?: unknown }).msg : null
        )
        .filter((msg): msg is string => typeof msg === "string" && msg.trim().length > 0);
      if (messages.length) return messages.join("; ");
    }
  } catch {
    // Not JSON (a proxy's HTML error page, an empty body) — use the fallback.
  }
  return fallback;
}

/** Longest response body carried into a fallback message. */
const MAX_FALLBACK_BODY = 200;

/**
 * The fallback when the body is not our JSON — bounded, so a proxy's HTML
 * error page cannot push the status out of view.
 *
 * `web/src/lib/api.ts` has since grown a fuller version of this (CF-304) and
 * an `ApiError` carrying `status` (CF-423). Both belong in the package rather
 * than a third copy here, so CF-314 should bring them across; this is the
 * bound only, because an unbounded message is the part that is actually wrong
 * rather than merely thinner.
 */
function fallbackMessage(status: number, body: string): string {
  const trimmed = body.trim();
  if (!trimmed || trimmed.startsWith("<")) return `API error ${status}`;
  return trimmed.length > MAX_FALLBACK_BODY
    ? `API error ${status}: ${trimmed.slice(0, MAX_FALLBACK_BODY)}…`
    : `API error ${status}: ${trimmed}`;
}

/**
 * Interim only — see the note at the top of this file. The real package has no
 * public `request`; when #418 lands, each call to this becomes a call to the
 * endpoint function for that route.
 */
export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const { baseUrl, getToken } = getApiClientConfig();
  // A token that cannot be read sends the request out unauthenticated rather
  // than failing it — what the web client does today, and what #418 keeps.
  const token = await Promise.resolve(getToken()).catch(() => null);

  // Built through `Headers` rather than spread into an object literal. A
  // `Headers` instance has no own enumerable properties, so `...init.headers`
  // spreads it to nothing: the caller's headers type-check and then vanish.
  // `Headers` takes all three forms `HeadersInit` allows.
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${baseUrl}${path}`, { ...init, headers });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(errorMessage(body, fallbackMessage(res.status, body)));
  }
  // Every DELETE in this API returns 204, and a 200 with no body happens too.
  // `res.json()` rejects on both with a parse error that reads as if the
  // response were malformed rather than absent.
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return (await res.json()) as T;
}
