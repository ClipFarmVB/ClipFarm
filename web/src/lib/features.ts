/**
 * Build-time feature flags.
 *
 * `NEXT_PUBLIC_*` is inlined by Next at build time, so these are constants in
 * the bundle rather than runtime lookups — flipping one needs a rebuild, not a
 * restart. That is also why the checks are safe in server components: there is
 * no request-time state involved.
 */

/**
 * Public identity: profiles, handles, avatars (CF-107).
 *
 * Off by default so the social epic can land incrementally without exposing the
 * surface in production. The API half is gated separately by `SOCIAL_ENABLED`
 * in `api/app/config.py`; with the API flag off the routes 404, so leaving this
 * on alone yields a UI that cannot load anything.
 */
export const SOCIAL_ENABLED = process.env.NEXT_PUBLIC_SOCIAL_ENABLED === "true";

/**
 * Whether a clip or a post may be set to `public` (CF-109b).
 *
 * Off by default, and mirrored from the API's `PUBLIC_POSTING_ENABLED` the same
 * way `SOCIAL_ENABLED` is mirrored — `api/app/services/publishing.py` carries
 * the argument for why that tier is gated apart from `followers`.
 *
 * This flag decides only what the composer OFFERS. The API refuses `public`
 * with a 422 regardless, and the composer still surfaces that: this is the
 * backstop for the two flags disagreeing, which is a rebuild apart on the web
 * side and a restart apart on the API side. An option that explains why it is
 * unavailable is a limit; one that fails on submit is a dead end — the same
 * argument the tier ceiling makes.
 */
export const PUBLIC_POSTING_ENABLED =
  process.env.NEXT_PUBLIC_PUBLIC_POSTING_ENABLED === "true";
