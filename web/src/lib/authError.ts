/** Query param the confirm route uses to tell `/login` why it bounced someone. */
export const AUTH_ERROR_PARAM = "auth_error";

/**
 * Auth failures travel as a code, never as a message.
 *
 * The wording lives here because anything read out of the URL and rendered on
 * our own login page is attacker-controlled text on a trusted-looking page —
 * a phishing surface. The URL only names which of these applies.
 */
const AUTH_ERRORS = {
  link_invalid: "That confirmation link is invalid.",
  link_expired: "That confirmation link has expired or was already used.",
  verification_failed: "We couldn't confirm your email. Please try signing in.",
} as const;

export type AuthErrorCode = keyof typeof AUTH_ERRORS;

/**
 * Unknown codes fall back to the generic message rather than rendering nothing.
 *
 * The own-property check is load-bearing: a plain index would inherit from
 * `Object.prototype`, so `?auth_error=__proto__` returns an object that React
 * refuses to render — a crafted link takes the whole login page down — and
 * `constructor` and friends return functions, which React drops silently.
 */
export function authErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  return Object.hasOwn(AUTH_ERRORS, code)
    ? AUTH_ERRORS[code as AuthErrorCode]
    : AUTH_ERRORS.verification_failed;
}
