/** Where a redirect falls back to when the requested destination isn't ours. */
export const DEFAULT_NEXT = "/games";

/**
 * A base to resolve against. Any absolute or protocol-relative destination
 * resolves to some other origin and gets rejected, so the value only has to be
 * a valid origin — it never appears in what we return.
 */
const RESOLUTION_BASE = "https://redirect.invalid";

/**
 * Reduce a caller-supplied `?next=` to a same-origin path.
 *
 * Prefix checks can't do this job: the URL parser normalizes `/\evil.com` to
 * `//evil.com`, and Next resolves hrefs with `new URL(href, location.href)`
 * before deciding a navigation is external — so a `startsWith("/")` guard still
 * hands the browser an off-site hard navigation. Resolving first and comparing
 * origins is the check that survives that normalization.
 */
export function safeNextPath(next: string | null | undefined): string {
  if (!next) return DEFAULT_NEXT;
  let url: URL;
  try {
    url = new URL(next, RESOLUTION_BASE);
  } catch {
    return DEFAULT_NEXT;
  }
  if (url.origin !== RESOLUTION_BASE) return DEFAULT_NEXT;
  return url.pathname + url.search + url.hash;
}
