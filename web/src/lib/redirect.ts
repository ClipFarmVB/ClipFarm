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
 *
 * Matching the origin is not enough on its own: it clears the authority that was
 * parsed, but the surviving pathname can start with `//` in its own right
 * (`//host//evil.com` resolves to pathname `//evil.com`), which becomes an
 * authority again the moment Next resolves it. Hence the second check.
 *
 * That one is a prefix check and, unlike on the raw input, a prefix check is
 * enough here: `pathname` always starts with `/` and never contains a backslash,
 * because the parser folds `\` into `/` for special schemes. Re-resolving
 * instead would strip only one authority level and let nesting through.
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

  const path = url.pathname + url.search + url.hash;
  if (path.startsWith("//")) return DEFAULT_NEXT;
  return path;
}
