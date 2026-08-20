/**
 * Turn an API error response into something worth showing a user.
 *
 * FastAPI wraps every rejection as `{"detail": "..."}`. The upload limits
 * (CF-91) write real explanations into that field — "you have 60 min of your
 * 360 min per 24 hours left" — which the old `Upload failed: 429 {…}` string
 * buried inside raw JSON.
 *
 * Only a plain string `detail` is used. Validation errors put an array of
 * objects there, which is for us, not the user, so those fall back.
 */
export function apiErrorMessage(body: string, fallback: string): string {
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail;
    }
  } catch {
    // Not JSON (a proxy's HTML error page, an empty body) — use the fallback.
  }
  return fallback;
}
