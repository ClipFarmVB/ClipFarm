import { buildAssetLinks } from "@/lib/wellKnown";

/**
 * `GET /.well-known/assetlinks.json` (CF-322).
 *
 * A route handler rather than a static file for the same reason as its iOS
 * sibling: the document carries the signing-key fingerprint, which is
 * deployment configuration. See `lib/wellKnown.ts` for why a missing or
 * malformed fingerprint 404s instead of being served.
 *
 * The route segment really is named `assetlinks.json` — Google fetches this at
 * that exact path, extension included.
 */

// Redundant on this version's defaults, kept for the reason the iOS sibling
// spells out.
export const dynamic = "force-dynamic";

export async function GET() {
  const assetLinks = buildAssetLinks(process.env);
  if (!assetLinks) return new Response(null, { status: 404 });

  return new Response(JSON.stringify(assetLinks), {
    headers: { "content-type": "application/json" },
  });
}
