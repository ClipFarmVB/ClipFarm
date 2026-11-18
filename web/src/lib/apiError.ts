/**
 * A failed API response, carrying its HTTP status (CF-423).
 *
 * `message` is exactly what `apiErrorMessage` produces — the server's own
 * sentence, or the bounded fallback — so every caller that only reads the
 * message sees what it saw before. `status` is what lets a caller tell "this
 * does not exist" (404) from "the server broke" (5xx) without matching on the
 * server's prose, which changes whenever someone rewords a `detail`.
 *
 * `retryAfterSeconds` is the response's `Retry-After`, parsed (CF-410): the
 * rate limiter (CF-186) sends it on a 429 so a caller can wait that long
 * instead of retrying blind. `null` when the header was absent or unreadable,
 * which is every response but the limiter's.
 *
 * Lives here rather than in `api.ts` so a component can `instanceof` it
 * without importing the client (and so a test that mocks `@/lib/api` still has
 * the real class). `api.ts` re-exports it.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Read a `Retry-After` header as whole seconds from `now`, or `null`.
 *
 * The header takes two forms (RFC 9110 §10.2.3): a non-negative integer of
 * seconds, which is what our limiter sends, and an HTTP-date, which only a
 * proxy in front of the API would. A date already past reads as 0. Anything
 * else — absent, negative, a decimal, garbage — is `null`, so a caller falls
 * back to its own wait rather than trusting a number nobody meant.
 */
export function parseRetryAfter(value: string | null | undefined, now: number = Date.now()): number | null {
  if (value == null) return null;
  const v = value.trim();
  if (/^\d+$/.test(v)) return Number(v);
  // Date.parse accepts bare numbers and other loose shapes; an HTTP-date
  // always names a weekday or month in letters, so require one.
  if (!/[A-Za-z]/.test(v)) return null;
  const at = Date.parse(v);
  if (Number.isNaN(at)) return null;
  return Math.max(0, Math.ceil((at - now) / 1000));
}

/**
 * Turn an API error response into something worth showing a user.
 *
 * FastAPI wraps every rejection as `{"detail": "..."}`. The upload limits
 * (CF-91) write real explanations into that field — "you have 60 min of your
 * 360 min per 24 hours left" — which the old `Upload failed: 429 {…}` string
 * buried inside raw JSON.
 *
 * Handles both shapes `detail` takes: a plain string for our deliberate 4xx,
 * and an array of `{loc, msg, type}` for a 422. The array branch lived only in
 * the post composer, so validation errors read nicely in exactly one form and
 * generically everywhere else.
 */
export function apiErrorMessage(body: string, fallback: string): string {
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail;
      // A 422 puts an array of {loc, msg, type} here. Previously those fell
      // back, so a validation failure showed a generic line everywhere — and
      // the post composer grew its own copy of this function to read them.
      // Joining `msg` is what the user can act on ("String should have at most
      // 500 characters"); `loc` and `type` are for us and stay out.
      if (Array.isArray(detail)) {
        const msgs = detail
          .map((d) => (d && typeof d === "object" ? (d as { msg?: unknown }).msg : null))
          .filter((m): m is string => typeof m === "string" && m.trim().length > 0);
        if (msgs.length) return msgs.join("; ");
      }
    }
  } catch {
    // Not JSON (a proxy's HTML error page, an empty body) — use the fallback.
  }
  return fallback;
}
