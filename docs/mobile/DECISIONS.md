# Mobile app — decision log

This file records the architectural choices behind the CF-313 mobile epic: the
ones that were made, and the ones that are still open. It exists because a diff
shows *what* was built and almost never *why*, and by the time the question
comes up again the reasoning has usually evaporated. Read the **Open** section
first — those are questions waiting on a human, and tickets are parked behind
some of them. Entries are newest first within each section, and entries are
**appended, never rewritten**: a correction is a new entry that links back to the
one it corrects, so the record of what was believed at the time stays intact.

---

## Open — needs Owen's call

### CF-320 · How a shared clip link survives being shared — 2026-09-11

**The choice.** `GET /clips/{id}/share` hands back a 1-hour presigned R2 URL.
CF-320 (#370) asks for a link that still plays tomorrow *and* stops playing when
the clip is un-shared. Those two properties cannot both come from a presigned
URL, so this is a choice about where a share link's authority lives — in the
storage layer, or in ours.

**Context.** The endpoint is `api/app/routers/clips.py:392`, and its own comment
already refuses to settle this:

> NOTE: still a 1h presigned URL even for public clips. CF-108's card flags
> revisiting this — a public clip's link is meant to be passed around, so a
> short expiry is user-hostile, while a long one is a bearer token nobody can
> revoke. Left as-is here rather than changed without a decision.

The sibling `/clips/{id}/download` (`clips.py:409`) mirrors the same 3600s
deliberately, "because answering it differently in two places would settle it by
accident" — so whatever is chosen here lands in both.

Three things make this urgent rather than academic. CF-337 (native share sheet)
is unbuildable on a link that dies in an hour — that is the whole point of the
ticket. CF-222 (public share links and rich embeds) wants a URL on our own
domain to hang Open Graph tags from. And the epic's sixth settled decision —
private by default, because this footage contains minors — is a statement about
*revocation*, which a presigned URL structurally cannot offer.

**Options.**

1. **Raise the expiry.** One constant; a 30-day presigned URL. It is the only
   option that ships this afternoon, and it fails the ticket's second acceptance
   box outright: un-sharing a clip cannot stop a URL that is already minted. It
   also hands out an unrevocable bearer token to footage of minors, which is the
   one thing the epic says not to do.

2. **Genuinely public objects behind unguessable keys, revoked by rotation** —
   the card's own guess. Playback costs us nothing and scales through the CDN.
   The costs are real, though: revocation means copying the object to a new key
   and deleting the old one, so it is a storage operation per un-share rather
   than a row update, and it is eventually consistent against any cache in
   front. More structurally, it moves "who may see this" out of `access.py` —
   where CF-108's deliberate asymmetry between clip and game visibility already
   lives — and into R2 object ACLs, leaving two sources of truth for one
   question.

3. **A ClipFarm-hosted share route.** A durable `/{s}/<token>` URL on our
   domain, backed by a row (clip, who shared it, when, revoked_at). The route
   checks the row and redirects to a freshly minted short presigned URL. Costs a
   table, an endpoint, and one extra hop per playback; needs care that the
   redirect preserves range requests so seeking still works.

**Chosen / recommended.** Option 3, and the deciding argument is not the one I
expected going in. Revocation is the stated acceptance criterion and only option
3 gives it synchronously, but the stronger reason is that **options 1 and 2 are
the irreversible ones.** Every link handed out under them is a capability we
cannot take back, so a later change of posture still leaves the old URLs live
forever. Option 3's links are rows, so a future change of mechanism can keep
honouring the ones already in the wild while minting a new shape — the thing
that looks like the most machinery is the one that preserves the most freedom.

Two smaller arguments point the same way. It keeps the authorization decision in
`access.py` rather than splitting it between the database and R2. And it puts
share links on the ClipFarm domain, which is precisely what the deep-link
association files need in order to open the app instead of the browser — so one
URL shape serves CF-320, CF-322, CF-326 and CF-222 at once.

**Consequences.** It adds a table and a migration, and the Alembic chain is
linear, so it has to be ordered against whatever else is in flight. It puts an
api hop in front of every shared playback — acceptable for a redirect, but it
means the share path now has an availability story it did not have before. It
overlaps CF-222 by construction: these should be one design, not two. And it
interacts with the CF-109b visibility work currently open in #480–#484 and with
CF-186 (#481), which is moving anonymous reads behind auth — the share route is
exactly an anonymous read, so the two need to agree on what it is allowed to do.

**Worth knowing.** The reason a share sheet changes this calculus at all is that
it hands the URL to software we do not control — Messages, Instagram, a group
chat — which may fetch it, cache it, or unfurl it days later, and will happily
show a broken video to people who never used ClipFarm. A presigned URL is a
*capability*: possession is permission, and there is no list of who holds one.
The only way to revoke a capability you have already handed out is to put
something you control in front of it and hand out a reference instead. That is
the whole of option 3, and it is the standard answer whenever a link escapes the
session that created it.

**Waiting on this:** CF-337 (#387), CF-326 (#376), CF-222, and the shape of
CF-320 itself. Nothing else in the epic blocks on it.

---

## Decided

<!-- newest first -->

### CF-322 · The association files are routes, and they fail closed — 2026-09-11

**The choice.** Serve `apple-app-site-association` and `assetlinks.json` from
Next route handlers built out of environment variables, and return **404** when
those variables are absent — rather than checking two static files into
`web/public/` with the identifiers written in.

**Context.** Neither identifier exists yet: there is no Apple Developer account
and no Play App Signing key, because CF-345 and CF-346 have not started. So the
ticket arrives with a shape but no values, and the question is what to do in the
gap. Two details of the files force the answer. The Apple file has **no
extension**, so a static asset gets whatever content type the file server
guesses, and Apple wants `application/json`. And the values are per-deployment
configuration, not source — the same repo has to be able to serve a staging
Team ID and a production one.

**Options.** Static files in `public/` with placeholder identifiers, which is
the least code and publishes values that are definitely wrong. Static files
plus a `headers()` rule in `next.config.ts` to fix the content type, which still
cannot vary per deployment. Or route handlers reading the environment, which is
about thirty lines and needs a small amount of care about when the environment
is read.

**Chosen.** Route handlers, failing closed. The deciding argument is the
asymmetry between the two failure modes, and it is much sharper here than it
looks. These files are not fetched the way a browser fetches a page: Apple's CDN
caches the association file and iOS re-reads it only on install and on app
update. So a wrong file published once keeps deciding link behaviour for a long
time after someone fixes it, on devices nobody can reach. A 404 costs one
`curl` to diagnose and nothing at all today, because the files are inert until
an app claims them. Publishing a placeholder Team ID buys nothing and risks the
sticky failure.

The same reasoning runs one level down, into `parseFingerprints`: a malformed
entry rejects the entire Android document rather than being dropped from it.
Serving a list quietly missing one fingerprint presents as "app links work from
my build and not from the Play build", which is the symptom the card names as
the most common Android failure and which costs days to chase.

**Consequences.** Both paths 404 in production until someone sets the three
variables, and `DEPLOY_RENDER.md` §6 is now where that is written down — the
ticket's two validator acceptance boxes cannot be ticked until then. These are
also the first server-only (non-`NEXT_PUBLIC_`) environment variables read from
`web/src`, so the prefix convention there is no longer universal. The Apple
allowlist is a small contract with CF-326, which may extend it.

**Worth knowing.** The generalisable half is the caching asymmetry, which is a
recurring shape in mobile that has no real web equivalent. On the web, a wrong
file is wrong until you fix it and then right. Anything the OS or an app store
reads at install time — association files, entitlements, store metadata — is
read rarely and cached aggressively, so the cost of a wrong value is measured in
app releases rather than in cache TTLs. That inverts the usual instinct: for
this class of file, serving nothing is safer than serving a guess.

---

### CF-322 · The iOS file claims an allowlist, not the whole domain — 2026-09-11

**The choice.** The `components` list names the paths the app may open and
explicitly excludes `/auth/*`, `/login` and `/signup`, instead of claiming
`/*`.

**Context.** Claiming the whole domain is the common default and it would break
signup. Supabase's confirmation email lands on `/auth/confirm`, which
`web/src/app/auth/confirm/route.ts` handles server-side — that route exists
precisely because the old PKCE link could only be opened in the browser that
started signup. If an installed app intercepted that link, confirmation would
break for anyone who signed up on a phone, and the link is single-use, so there
is no second attempt and nothing tells the user what went wrong.

**Options.** Claim `/*` and handle unknown paths in the app by bouncing back out
to the browser — which does not work, because the app cannot hand a single-use
token back to the browser that needs it. Or claim an allowlist.

**Chosen.** The allowlist, with the exclusions ordered first. The ordering is
not cosmetic: Apple evaluates `components` top to bottom and stops at the first
match, so an exclusion listed after the include it carves out of does nothing,
silently. There is a unit test pinning that ordering, because it is the kind of
mistake that survives review and only shows up as "sign-up is broken on iPhones".

**Consequences.** Every new deep-linkable URL shape needs a line here. That is a
real cost and the right side of the trade — the failure mode of forgetting a
line is "the link opens the browser", while the failure mode of `/*` is "the
link opens the app and the user cannot finish signing up".

**Worth knowing.** Universal links have no negotiation step: the OS decides
from a static file, at install time, whether your app or the browser gets a URL,
and the app cannot decline and pass it back. So anything that must be handled by
the web — OAuth callbacks, email confirmations, payment returns — has to be
carved out in the association file itself. This is the single most common way a
team breaks its own auth flow by shipping an app.
