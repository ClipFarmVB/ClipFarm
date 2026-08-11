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
