import { DEFAULT_NEXT } from "@/lib/redirect";

// Routes that require an authenticated session
// /settings is protected; /u/{handle} deliberately is not — a profile has to be
// reachable by someone who isn't signed in (or isn't following) for the account
// to be findable at all. What's *visible* there is gated separately (CF-108).
export const PROTECTED_PREFIXES = ["/games", "/upload", "/collections", "/settings"];

export function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(prefix + "/")
  );
}

/**
 * Where the middleware should send this request, or null to let it through.
 *
 * Pure so it can be tested without a Supabase client or a NextRequest. The
 * return value is a path plus query, resolved against the request URL by the
 * caller.
 *
 * - `/` is the landing page for signed-out visitors (CF-219). A signed-in user
 *   goes straight to the app rather than through marketing.
 * - Protected routes send a signed-out visitor to /login, carrying where they
 *   were headed in `?next=`.
 */
export function authRedirect(pathname: string, signedIn: boolean): string | null {
  if (pathname === "/" && signedIn) return DEFAULT_NEXT;
  if (!signedIn && isProtectedPath(pathname)) {
    return `/login?next=${encodeURIComponent(pathname)}`;
  }
  return null;
}
