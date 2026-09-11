import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

// Routes that require an authenticated session
// /settings is protected; /u/{handle} deliberately is not — a profile has to be
// reachable by someone who isn't signed in (or isn't following) for the account
// to be findable at all. What's *visible* there is gated separately (CF-108).
const PROTECTED_PREFIXES = ["/games", "/upload", "/collections", "/settings"];

export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          // Write refreshed cookies back to both the outgoing request and response
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options)
          );
        },
      },
    }
  );

  // getUser() validates the JWT server-side (not just reads from cookie)
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const { pathname } = request.nextUrl;
  const isProtected = PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(prefix + "/")
  );

  if (isProtected && !user) {
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = "/login";
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return response;
}

export const config = {
  // Run on all routes except Next.js internals, static files, the Sentry
  // tunnel (/monitoring) — every browser error event POSTs there and must not
  // trigger a Supabase auth check — and `/.well-known/*`.
  //
  // `.well-known` is excluded for the association files (CF-322). It is not
  // fixing a live bug — `.well-known` matches no PROTECTED_PREFIXES entry, so
  // this middleware falls through to a 200 for it today. It buys two things.
  // It spares every association fetch a `supabase.auth.getUser()` round trip it
  // has no use for. And it forecloses a failure that would be very hard to
  // diagnose: Apple's fetcher does not follow redirects, so a later prefix
  // added above that happened to cover `.well-known` would break iOS deep links
  // with no error visible anywhere, since the fetch is made by the OS at
  // install time rather than by anything we can watch.
  matcher: [
    "/((?!_next/static|_next/image|favicon\\.ico|monitoring|\\.well-known|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
