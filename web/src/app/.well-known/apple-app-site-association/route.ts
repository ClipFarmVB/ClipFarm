import { buildAppleAssociation } from "@/lib/wellKnown";

/**
 * `GET /.well-known/apple-app-site-association` (CF-322).
 *
 * A route handler rather than a file in `public/`, for two reasons the card is
 * specific about. The file has **no extension**, so a static asset would be
 * served with whatever content type the file server guesses, and Apple wants
 * `application/json`. And the document has to carry the Team ID, which is
 * deployment configuration rather than source — see DEPLOY_RENDER.md.
 *
 * Apple's fetcher does not follow redirects, so this must answer 200 directly.
 * Nothing in the app redirects it today — `next.config.ts` sets no `redirects`,
 * `rewrites` or `trailingSlash`, and the Supabase middleware only redirects
 * PROTECTED_PREFIXES — but see `src/middleware.ts` for why `.well-known` is
 * kept out of the matcher anyway. At the edge it is a live concern: the `www`
 * to apex redirect in DEPLOY_RENDER.md §4 means this has to be fetched from the
 * apex.
 */

// Redundant in Next 16 — `GET` route handlers have defaulted to dynamic since
// v15, and this one is dynamic on that default alone (verified in the build
// output). Stated anyway because the property matters: these identifiers are
// deployment configuration and must be read per request, so a future version
// that reverts to static `GET` handlers would silently bake in whatever the
// Render build container had set.
export const dynamic = "force-dynamic";

export async function GET() {
  const association = buildAppleAssociation(process.env);
  if (!association) return new Response(null, { status: 404 });

  return new Response(JSON.stringify(association), {
    // Spelled out rather than left to `Response.json()`: this exact value is
    // half of what the ticket asks for, so it should be readable here.
    headers: { "content-type": "application/json" },
  });
}
