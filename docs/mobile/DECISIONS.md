# Mobile app — decision log

The architecture of the mobile app (epic CF-313, #363), decided one ticket at a
time and written down as it happens. Two sections: **Open** is a question that
needs Owen's answer before the work it blocks can proceed — nothing is
implemented against an open entry, because a provisional architectural choice
with a PR wrapped around it is the thing that gets rubber-stamped. **Decided** is
a choice already made and shipped, recorded so the next reader does not have to
reverse-engineer the reasoning out of a diff. Newest first in both. Entries are
appended, never rewritten: correcting one means a new entry that links back to
it, so the record shows what was believed at the time and not just what turned
out to be true.

## Open — needs Owen's call

*Nothing open.*

## Decided

### CF-319 · What a playback refresh returns, and how it reports expiry — 2026-09-11

**The choice.** `GET /clips/{clip_id}/playback` returns a small object —
`clip_id`, `clip_url`, `thumbnail_url`, `expires_in`, `expires_at` — rather than
a bare `{"url": ...}` like its two siblings `/share` and `/download`, and it
reports the expiry in the response rather than only documenting it.

**Context.** Every URL the api hands a player is presigned for an hour. On the
web that is invisible: the page reloads long before it matters. On a phone it is
not — an app backgrounded for two hours comes back to a player whose URL 403s,
and before this endpoint the only recovery was refetching the whole clip list to
get the same rows back with fresh signatures on them. The card (#369) asked for
three things: an authorized viewer can exchange a clip id for a fresh URL,
authorization goes through the existing `_get_viewable_clip` rather than a second
implementation, and the expiry is documented so a client can refresh *before*
failure rather than after.

That third one is what forces a response shape. `/share` returns `{"url": ...}`
and says nothing about when it dies, which is precisely the client behaviour
being fixed: a client that discovers expiry by failing shows a dead player every
time, where a client told the TTL refreshes on a timer and never shows one.

**Options.** Match `/share` exactly and put the expiry only in the docs — the
cheapest and most consistent with the neighbours, but it leaves the mobile client
hardcoding `3600` on its side, which is a number in two repositories' worth of
places that must agree forever. Return the full `ClipOut` with fresh URLs — the
client gets everything, but it invites two copies of the same clip disagreeing
about fields the refresh never looked at, and it would have had to route through
`_clip_out`, whose derived-field invariant (`effective_visibility`,
`source_available`) exists for a different job. Or return only what actually
expires, plus the expiry — one more schema to maintain, and it makes the new
endpoint the only one of the three clip-URL routes with a response model.

**Chosen.** The third. The deciding argument is that the TTL is the *contract*
here, not an implementation detail: the whole value of the endpoint is a client
that can schedule its own refresh, and a contract a client has to hardcode is one
that breaks silently on the day the server changes it. Reporting it costs one
field. `expires_at` is sent alongside `expires_in` because a client that was
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
