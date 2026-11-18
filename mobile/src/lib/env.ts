/**
 * Build-time configuration.
 *
 * `EXPO_PUBLIC_*` is inlined into the bundle by Metro, exactly like the web
 * app's `NEXT_PUBLIC_*` — so these are constants in the bundle rather than
 * runtime lookups, changing one needs a rebuild, and nothing secret can live
 * here. See mobile/.env.example.
 */

/**
 * Fail with the name of the variable and where to set it.
 *
 * An unset Supabase URL otherwise surfaces as `createClient` throwing
 * "Invalid URL" from inside the library, on the first screen, with no hint
 * that a `.env` file is what's missing.
 */
function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `${name} is not set. Copy mobile/.env.example to mobile/.env and fill it in.`
    );
  }
  return value;
}

/**
 * Where the ClipFarm API lives.
 *
 * Defaulted only in development. A release build that reaches this unset used
 * to fall back to `http://localhost:8000`, which no phone can reach and
 * Android blocks as cleartext anyway — so the app looked network-broken when
 * the real fault was a missing EAS environment variable. Failing here names
 * the variable instead. See the EAS section of mobile/README.md.
 */
export const API_URL = (): string =>
  __DEV__
    ? (process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8000")
    : required("EXPO_PUBLIC_API_URL", process.env.EXPO_PUBLIC_API_URL);

export const SUPABASE_URL = (): string =>
  required("EXPO_PUBLIC_SUPABASE_URL", process.env.EXPO_PUBLIC_SUPABASE_URL);

export const SUPABASE_ANON_KEY = (): string =>
  required("EXPO_PUBLIC_SUPABASE_ANON_KEY", process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY);

/**
 * Public identity: profiles, feed, follows (CF-107), mirroring
 * `web/src/lib/features.ts`. Off by default. The API half is gated separately
 * by `SOCIAL_ENABLED` in `api/app/config.py`; with that off the routes 404, so
 * the social tabs stay hidden rather than rendering a screen that cannot load.
 */
export const SOCIAL_ENABLED = process.env.EXPO_PUBLIC_SOCIAL_ENABLED === "true";
