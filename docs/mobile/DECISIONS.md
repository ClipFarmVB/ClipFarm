# Mobile app — decision log

This file records the architectural choices made while building the mobile app
epic (CF-313, #363), so the shape of the thing can be understood without
reverse-engineering it from diffs. **Open** entries are questions waiting on
Owen — work that depends on them is stopped, not guessed at. **Decided**
entries were settled by whoever was implementing, and are here because a future
reader would otherwise have to work out *why* from the code alone. Newest
first in both sections.

Entries are appended, never rewritten. Correcting an earlier one means a new
entry that links back to it, so the reasoning stays legible in order.

The epic body has its own **"Decisions already made (do not relitigate)"**
section — Expo over Capacitor, one repo, both platforms, the `ClipSource`
interface, transactional-vs-engagement push, private by default. Those are
settled and are not repeated here; this file only covers what came up while
building.

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

### CF-318 · How a push notification actually gets delivered — 2026-09-10

**The choice.** CF-318's second half sends "your game is ready" when
`process_game_task` reaches a terminal state. There are two ways for our server
to reach a phone, and they are not variations on each other: send through
**Expo's Push Service**, or talk to **APNs and FCM directly**. The registry
half of the card is built and merged-ready either way; the send half is stopped
here until this is answered.

**Context.** The epic settles that transactional push ships in the core app and
engagement push waits for the social layer, but not how either is delivered.
The decision has to be made now rather than later because it is what CF-325
(#375) hands a token *of*: the app registers an Expo token or a native APNs/FCM
token, and the two are different strings obtained through different code. The
registry deliberately does not care — `token` is opaque `Text` and `platform`
is just `ios`/`android` — so this question is genuinely open and costs nothing
to leave open for now. It stops being cheap the moment either the send path or
CF-325 is written.

**Options.**

*Expo Push Service.* One HTTPS call to Expo with a list of tokens; Expo holds
the APNs key and the FCM credentials and fans out. Cheapest to build — no JWT
signing, no connection pooling, no two-provider error taxonomy — and it hands
back a per-token receipt that says `DeviceNotRegistered`, which is exactly the
prune signal the card's last acceptance line asks for. The cost is a third
party in the delivery path for the one feature the epic names as the app's
second reason to exist, on a free tier with a rate limit we would not control,
and it is the harder direction to leave: tokens are Expo-format, so migrating
off means re-registering every device in the field.

*Direct APNs + FCM.* Two integrations. APNs is an HTTP/2 connection with a
JWT signed by a `.p8` key, rotated hourly; FCM v1 is a Google service account
and an OAuth token. Roughly a day and a half more work, two sets of failure
handling, two token-invalidation formats to normalise. What it buys is no
intermediary, no shared rate limit, and native tokens — which are the ones any
future provider also wants, so the direction is reversible in a way the other
is not.

*(A managed sender — OneSignal, Firebase-only, a queue product — is a third
shape, but it has the Expo trade-off with an extra vendor and no offsetting
benefit for two platforms we already have credentials for. Not worth a column.)*

**Recommended.** **Expo Push Service, for now** — but the recommendation is
weaker than it looks and the reason to confirm it deliberately is the exit
cost. We are already committed to Expo for the client (epic decision 1), so
this adds no new vendor relationship, and the receipt API removes the single
fiddliest part of the send path. CF-325's own body already lists the APNs `.p8`
and the Firebase `google-services.json` as prerequisites, and Expo needs those
same credentials uploaded to it — so choosing Expo does not save the account
setup, only the sending code. The one thing that would flip me is if push
volume is ever expected to run ahead of Expo's limits, which for
one-notification-per-game-processed it will not for a long time.

**Consequences.** Whichever is chosen decides what CF-325 (#375) registers and
what CF-342 (#392) later reuses, and it is the reason both are worth holding
until this is answered. Reversing from Expo to direct means every device in the
field re-registers a different token — survivable while the user base is small,
which is an argument for deciding it now rather than after launch. Reversing
the other way is nearly free. The registry table itself is unaffected either
way, by design.

**Worth knowing.** A push token is not a credential and not a stable id — it is
a routing address the OS reissues at will (reinstall, restore from backup,
sometimes an OS update), which is why the client is expected to re-register on
every launch and why the server treats an unrecognised token as a prune rather
than an error. The layer that matters here is *who holds the signing key*: APNs
will only accept a payload signed by our Apple key, so anything that sends on
our behalf holds that key. That is the actual substance of the choice, more
than the lines of code.

---

## Decided

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

---

### CF-319 · What a playback refresh returns, and how it reports expiry — 2026-09-11

**The choice.** `GET /clips/{clip_id}/playback` returns a small object —
`clip_id`, `clip_url`, `thumbnail_url`, `expires_in`, `expires_at` — rather than
a bare `{"url": ...}` like its two siblings `/share` and `/download`, and it
reports the expiry in the response rather than only documenting it.

**Context.** Every URL the api hands a player is presigned for an hour. On the
web that is invisible: the page reloads long before it matters. On a phone it is
not — an app backgrounded for two hours comes back to a player whose URL 403s,
and before this endpoint the only recovery was refetching the whole clip list to
get the same rows back with new signatures on them. The card (#369) asked for
three things: an authorized viewer can exchange a clip id for a fresh URL,
authorization goes through the existing `_get_viewable_clip` rather than a
second implementation, and the expiry is documented so a client can refresh
*before* failure rather than after.

That third one is what forces a response shape. `/share` returns `{"url": ...}`
and says nothing about when it dies, which is precisely the client behaviour
being fixed: a client that discovers expiry by failing shows a dead player every
time, where a client told the TTL refreshes on a timer and never shows one.

**Options.** *Match `/share` exactly and put the expiry only in the docs* — the
cheapest and most consistent with the neighbours, but it leaves the mobile
client hardcoding `3600` on its side, a number that then has to agree forever
across two codebases. *Return the full `ClipOut` with fresh URLs* — the client
gets everything, but it invites two copies of the same clip disagreeing about
fields the refresh never looked at, and it would have to route through
`_clip_out`, whose derived-field invariant (`effective_visibility`,
`source_available`) exists for a different job. *Return only what actually
expires, plus the expiry* — one more schema to maintain, and it makes the new
endpoint the only one of the three clip-URL routes with a response model.

**Chosen.** The third. The deciding argument is that the TTL is the *contract*
here, not an implementation detail: the whole value of the endpoint is a client
that can schedule its own refresh, and a contract a client has to hardcode is
one that breaks silently on the day the server changes it. Reporting it costs
one field. `expires_at` is sent alongside `expires_in` because a client that was
backgrounded cannot trust a duration it received at an unknown time in the past.

Two smaller calls folded in. The thumbnail comes along because it goes stale on
exactly the same clock and a second round-trip for a poster is the same bug in
miniature. And `expires_in`/`expires_at` are **null** when R2 is unconfigured —
the dev path serves a stored public URL that has no signature and so no expiry.
Reporting an hour there would have a client refresh a URL that never dies, and,
worse, teach it that a non-null expiry means "signed".

**Consequences.** `PLAYBACK_URL_TTL_SECONDS` is now a named constant in
`api/app/routers/clips.py` and the expiry is part of the api's public contract:
changing it is a client-visible change, not a one-line tweak. It is deliberately
scoped to *playback* — `/share` and `/download` keep their own `3600` literals,
because /share's expiry is the open question CF-320 (#370) exists to settle and
folding all three into one constant would let that decision change playback by
accident. Same number today, different reasons for it. Reversing any of this is
cheap: the endpoint is additive, nothing else reads it yet, and the fields are
new rather than changed.

It also adds a sixth unauthenticated read where `services/access.py` enumerates
five, and CF-186 (#481) is in flight adding rate limits to those five without
knowing about this one. Filed as CF-379 (#494) rather than guessed at, because
the choice there — throttle it like `/share`, or put it behind auth as #481 does
for `/download` — is a real one and belongs with that card.

What this does **not** do is make the URL durable. `posts._serialize` already
records the real ceiling: a presigned URL is bearer authority for its lifetime,
so narrowing a clip's visibility stops the api serving it and does nothing to a
URL already handed out, and the honest revocation window stays "up to an hour".
The alternative that fixes that — a stable URL through an endpoint that
re-checks visibility per request, with the object private — is a storage change
and belongs to CF-320, which owns exactly that decision. CF-319 makes the
existing model survivable on a phone; it does not replace it.

**Worth knowing.** The reason this ticket exists at all is a mobile fact with no
web equivalent: a backgrounded app is *suspended*, not running slowly. It does
not get to notice that an hour passed, refresh quietly, and carry on — it is
frozen, and then resumed into a world where its state is stale all at once. So
anything time-limited a mobile client holds needs either a way to tell how much
time is left (this endpoint) or a way to recover after the fact. Designing for
"the process was paused for two hours and woke up mid-screen" is most of what
makes a native client's data layer differ from a web one's.

### CF-318 · The token identifies the device, and registering re-owns it — 2026-09-10

**The choice.** `device_tokens.token` is unique across the whole table rather
than per user, and `POST /devices/tokens` upserts on it — so registering a
token that already belongs to someone else moves the row to the new caller
instead of creating a second one or refusing.

**Context.** CF-343 (#393) already requires sign-out to unregister the token,
with the right reason: "otherwise the next person to sign in on that device
gets someone else's notifications." But sign-out is a request, and requests
fail, get made offline, or never happen at all — an uninstall sends nothing.
Since this footage is youth sports, a stranger's phone announcing another
family's game is the kind of leak the epic's private-by-default position exists
to prevent, so it needs a structural answer and not only a well-behaved client.

**Options.** *Unique on `(owner_id, token)`* — the obvious shape, and wrong:
one device accumulates a row per account that ever signed into it, and every
one of them keeps receiving. *Unique on `token`, refuse a conflict with 409* —
safe against theft but leaves the resold phone attached to its old owner, i.e.
it preserves exactly the failure we are trying to remove, and does it silently.
*Unique on `token`, re-own on conflict* — what was built.

**Chosen.** Re-own. The two risks are not symmetric. Re-owning costs an
attacker who *already holds someone's push token* the ability to receive "your
game is ready" in their place — a low-value payload, and they had to obtain the
token first. Refusing costs a resold phone's new owner nothing at all and leaks
the previous owner's activity to them with no action by anybody, which is the
likelier event by a wide margin and the worse one. `created_at` is deliberately
left out of the `SET` clause so a re-registration keeps its first-seen date;
`last_seen_at` moves.

**Consequences.** CF-325 (#375) can call register on every launch without
checking anything first, which is what you want given the OS reissues tokens
unannounced. CF-343's sign-out unregister stays worth doing — it is the prompt
path — but is no longer the only thing standing between a device and the wrong
owner. Reversing this means a migration to drop the unique constraint and a
decision about what to do with the rows already merged under it.

**Worth knowing.** The instinct from web sessions is that a credential belongs
to a user, so you scope it by user. Push tokens invert that: the OS issues them
per *app install on a device*, so the device is the primary key and the user is
an attribute of it that changes over time. Modelling it the web way is a common
first draft and produces a table that looks correct and quietly multi-notifies.

### CF-318 · Unregister is `POST /devices/tokens/unregister` — 2026-09-10

**The choice.** The unregister endpoint is a POST with a JSON body, not
`DELETE /devices/tokens` with a body and not `DELETE /devices/tokens/{token}`.

**Context.** It is called on sign-out (CF-343), which makes it the half of the
pair that must not fail quietly — a stripped request that still returns 204
leaves the token registered and produces the leak the entry above is about.

**Options.** *`DELETE` with a body* reads best, but RFC 9110 gives a payload on
DELETE no defined semantics and explicitly permits intermediaries to drop it;
we sit behind a platform proxy in production. *Token in the path* survives any
intermediary but writes the token into every access log, and Expo's
`ExponentPushToken[...]` spelling needs percent-encoding to get through a path
segment intact. *POST to a named sub-resource* has neither problem and is a
shape no proxy treats specially.

**Chosen.** The POST. It is slightly less pretty and cannot be silently
defeated, and on this particular route that trade is not close. Both endpoints
return 204 unconditionally — a token that was never registered, or belongs to
someone else, is not an error — so sign-out has no failure mode to handle and
the route cannot be used to ask whether a given token is registered.

**Worth knowing.** "DELETE with a request body" is widely used and works in
most stacks, which is what makes it a trap: it fails only in the presence of a
particular intermediary, which means it fails in production and not in
development.
