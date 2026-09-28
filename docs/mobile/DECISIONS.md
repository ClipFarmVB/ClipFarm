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
