/**
 * The two association files that let a ClipFarm link open the app instead of
 * the browser (CF-322).
 *
 * Both documents are built here as plain data so they can be tested without a
 * server: the route handlers under `app/.well-known/` do nothing but call these
 * and set a content type.
 *
 * **Both fail closed.** A builder returns `null` when its identifiers are
 * missing or malformed, and the route then 404s rather than serving a document
 * with placeholder values in it. That is the safer failure for these two files
 * specifically, because neither platform fetches them the way a browser fetches
 * a page: Apple's CDN caches the association file and the OS re-reads it only
 * on install and on app update, so a wrong file published once keeps opening
 * (or refusing to open) links long after it is fixed. A 404 is diagnosable with
 * one `curl`; a cached wrong answer is not.
 */

/**
 * Just the slice of the environment these builders read. `process.env`
 * satisfies it, and so does a bare object literal in a test — which
 * `NodeJS.ProcessEnv` does not, since it requires `NODE_ENV`.
 */
type Env = Readonly<Record<string, string | undefined>>;

/** `<TeamID>.<bundleIdentifier>` — what Apple calls the app ID. */
const IOS_APP_ID = "IOS_APP_ID";
const ANDROID_PACKAGE_NAME = "ANDROID_PACKAGE_NAME";
const ANDROID_SHA256_CERT_FINGERPRINTS = "ANDROID_SHA256_CERT_FINGERPRINTS";

/**
 * URL paths the iOS app claims, in Apple's evaluation order.
 *
 * Apple matches `components` top to bottom and stops at the first hit, so every
 * `exclude` has to precede the includes it carves out of — a list that reads
 * correctly but is ordered wrongly silently claims the excluded paths.
 *
 * This is an allowlist rather than `/*` on purpose. Supabase's email
 * confirmation lands on `/auth/confirm` and has to be handled by the web app
 * (`src/app/auth/confirm/route.ts` establishes the session server-side); if the
 * installed app intercepted that link instead, confirmation would break for
 * anyone who signed up on a phone, and the link is single-use so there is no
 * second try. `/login` and `/signup` are excluded for the same reason — they
 * are where a failed confirmation redirects to.
 *
 * CF-326 owns routing these to screens and may extend the list; extending it is
 * a one-line change here plus a redeploy, but see the caching note above for
 * why removals take a while to take effect.
 *
 * **One path shape is knowingly missing.** A shared clip has no durable URL on
 * this domain yet — `/clips/{id}/share` mints a 1h presigned R2 link, and what
 * replaces it is the open question in `docs/mobile/DECISIONS.md` (CF-320). If
 * that lands as a token route rather than `/clips/*`, its prefix has to be
 * added here or a tapped share link will open the browser rather than the app,
 * which is the one journey CF-337 exists to make work.
 *
 * Android does no path filtering in this file at all — `assetlinks.json` grants
 * the app the whole domain, and the app's own intent filters decide which paths
 * it opens. So this list is genuinely iOS-only, not a shared contract.
 */
export const APPLE_COMPONENTS: readonly AppleComponent[] = [
  { "/": "/auth/*", exclude: true, comment: "Web must handle email confirmation" },
  { "/": "/login", exclude: true, comment: "Where a failed confirmation lands" },
  { "/": "/signup", exclude: true, comment: "Where a failed confirmation lands" },
  { "/": "/games/*" },
  { "/": "/clips/*" },
];

export interface AppleComponent {
  "/": string;
  exclude?: true;
  comment?: string;
}

export interface AppleAssociation {
  applinks: {
    apps: never[];
    details: { appIDs: string[]; components: readonly AppleComponent[] }[];
  };
}

export interface AssetLink {
  relation: string[];
  target: {
    namespace: "android_app";
    package_name: string;
    sha256_cert_fingerprints: string[];
  };
}

/** 32 colon-separated hex bytes, which is the only shape either tool emits. */
const FINGERPRINT = /^[0-9A-F]{2}(:[0-9A-F]{2}){31}$/;

/**
 * Split the configured fingerprints, or return `null` if any one of them is not
 * a SHA-256 fingerprint.
 *
 * Rejecting the whole list rather than dropping the bad entry is deliberate. A
 * served file that is missing one fingerprint presents as "app links work from
 * my local build and not from the Play build", which is the exact symptom the
 * card names as the single most common reason Android app links fail silently,
 * and it costs days. A 404 costs one `curl`.
 */
export function parseFingerprints(raw: string | undefined): string[] | null {
  if (!raw) return null;
  const parsed = raw
    .split(",")
    .map((entry) => entry.trim().toUpperCase())
    .filter((entry) => entry.length > 0);
  if (parsed.length === 0) return null;
  return parsed.every((entry) => FINGERPRINT.test(entry)) ? parsed : null;
}

/**
 * Apple's `apple-app-site-association`.
 *
 * Emitted in the `appIDs`/`components` form (iOS 13+) rather than the older
 * `appID`/`paths` one. A first release in 2026 has no reason to carry a
 * deployment target that old, and carrying both forms means two lists that can
 * disagree. `apps: []` is the one piece of the legacy shape kept, because it
 * costs nothing and some validators still look for it.
 */
export function buildAppleAssociation(env: Env): AppleAssociation | null {
  const appId = env[IOS_APP_ID]?.trim();
  if (!appId) return null;
  return {
    applinks: {
      apps: [],
      details: [{ appIDs: [appId], components: APPLE_COMPONENTS }],
    },
  };
}

/**
 * Android's `assetlinks.json`.
 *
 * The fingerprint must be the **Play App Signing** key's, not the upload key's.
 * Google re-signs every build with the former, so the upload key's fingerprint
 * is the one a developer has locally and the wrong one to publish here. Its
 * provenance belongs in a comment next to the deployed value — see
 * DEPLOY_RENDER.md.
 */
export function buildAssetLinks(env: Env): AssetLink[] | null {
  const packageName = env[ANDROID_PACKAGE_NAME]?.trim();
  const fingerprints = parseFingerprints(env[ANDROID_SHA256_CERT_FINGERPRINTS]);
  if (!packageName || !fingerprints) return null;
  return [
    {
      relation: ["delegate_permission/common.handle_all_urls"],
      target: {
        namespace: "android_app",
        package_name: packageName,
        sha256_cert_fingerprints: fingerprints,
      },
    },
  ];
}
