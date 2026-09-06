import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import type { EmailOtpType } from "@supabase/supabase-js";
import { AUTH_ERROR_PARAM, type AuthErrorCode } from "@/lib/api";

/**
 * Email confirmation, verified server-side.
 *
 * The PKCE link this replaces could only be opened in the browser that started
 * signup: the code verifier lives in that browser's storage, so tapping the
 * link on a phone after signing up on a laptop failed with `bad_code_verifier`.
 * A `token_hash` carries everything verification needs, so any browser works —
 * and the session is established here rather than by whatever the link happened
 * to land on.
 *
 * Inert until the Supabase "Confirm signup" template emits a link at this
 * route; see DEPLOY_RENDER.md.
 */

/** Where a confirmed link lands. Not caller-supplied — see `lib/redirect.ts`. */
const DESTINATION = "/games";

/**
 * Deliberately narrower than auth-js's `EmailOtpType`.
 *
 * Every type accepted here ends the same way: session established, user sent to
 * `DESTINATION`. A type whose link should land somewhere else must not be
 * accepted until that somewhere exists. `recovery` is the live example — it
 * would verify, sign the user in and drop them on `/games` with the password
 * they forgot still set, and the link is single-use, so nothing tells them it
 * went wrong. Unwired types fail closed as `link_invalid` instead.
 *
 * Add a type here when the template that emits it is wired up.
 */
const ACCEPTED_OTP_TYPES = ["signup"] as const satisfies readonly EmailOtpType[];

type AcceptedOtpType = (typeof ACCEPTED_OTP_TYPES)[number];

function isAcceptedOtpType(value: string | null): value is AcceptedOtpType {
  return value !== null && (ACCEPTED_OTP_TYPES as readonly string[]).includes(value);
}

/** Built from `nextUrl` rather than `request.url` so it keeps the proxied host. */
function redirectTo(request: NextRequest, pathname: string, params?: Record<string, string>) {
  const url = request.nextUrl.clone();
  url.pathname = pathname;
  url.search = "";
  for (const [key, value] of Object.entries(params ?? {})) url.searchParams.set(key, value);
  return NextResponse.redirect(url);
}

function failure(request: NextRequest, code: AuthErrorCode) {
  return redirectTo(request, "/login", { [AUTH_ERROR_PARAM]: code });
}

export async function GET(request: NextRequest) {
  const tokenHash = request.nextUrl.searchParams.get("token_hash");
  const type = request.nextUrl.searchParams.get("type");

  if (!tokenHash || !isAcceptedOtpType(type)) return failure(request, "link_invalid");

  // The session cookies have to ride out on the response we return, so it
  // exists before the client that writes to it.
  const response = redirectTo(request, DESTINATION);

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options)
          );
        },
      },
    }
  );

  const { error } = await supabase.auth.verifyOtp({ token_hash: tokenHash, type });
  if (error) {
    return failure(request, error.code === "otp_expired" ? "link_expired" : "verification_failed");
  }

  return response;
}
