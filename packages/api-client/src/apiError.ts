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
