import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { authRedirect } from "@/lib/authRoutes";

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
  const target = authRedirect(pathname, Boolean(user));
  if (!target) return response;

  // Keep the request's own query and overlay the target's, as the /login
  // redirect always has.
  const url = request.nextUrl.clone();
  const resolved = new URL(target, url);
  url.pathname = resolved.pathname;
  resolved.searchParams.forEach((value, key) => url.searchParams.set(key, value));
  const redirect = NextResponse.redirect(url);

  // A signed-in visitor to `/` may just have had their session refreshed by
  // getUser(). Those cookies were written onto `response`, which a redirect
  // replaces — carry them across or the refreshed token is lost. The signed-out
  // /login redirect has the same gap; that is a separate fix.
  if (user) {
    response.cookies.getAll().forEach((cookie) => redirect.cookies.set(cookie));
  }
  return redirect;
}

export const config = {
  // Run on all routes except Next.js internals, static files, and the Sentry
  // tunnel (/monitoring) — every browser error event POSTs there and must not
  // trigger a Supabase auth check.
  matcher: [
    "/((?!_next/static|_next/image|favicon\\.ico|monitoring|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
