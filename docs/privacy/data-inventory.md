# Personal-data inventory

**This is not a privacy policy, and it is not legal advice.** It is the
engineering input to one: what personal data ClipFarm stores, where it lives,
who can see it, and what happens to it when an account goes away. CF-88
needs a lawyer; a lawyer needs this first, because a policy drafted against
a guess describes a system that does not exist.

Everything below is read off the schema and the routers. `api/tests/
test_personal_data_inventory.py` fails when a `users` column or a table
referencing it appears, disappears, or changes its delete behaviour without
the test's own copy of the inventory being updated. That copy is two dicts in
the test: the `users` columns with their classes, and the tables referencing
`users.id` with their delete behaviour. No test reads this file, so every
part of it, §1's table and §3's table included, goes stale unnoticed when a
change updates the models and the test's copy together. Two of the test's
failure messages name this file; keeping it in step is a human's job in the
same PR.

---

## 1. What is stored about a person

All on the `users` table unless noted.

| Column | Class | Notes |
| --- | --- | --- |
| `id` | operational | Internal UUID, and **not** an internal-only one: `ProfileOut` returns it, `GET /users/{handle}` is unauthenticated, and `avatar_url` is stored as `{R2 public URL}/avatars/{user_id}`, so the UUID reaches anonymous callers both as a field and inside a URL. Unguessable, which is a different property from undisclosed. |
| `email` | **identifier** | Unique, required. Copied from the Supabase token on the first authenticated request and never updated after (`_ensure_user_exists` inserts and does nothing on conflict), so an email changed in Supabase does not reach this column; a token with no email claim stores `{sub}@unknown`. The only field that reaches a real person off-platform. |
| `hashed_password` | **credential** | Nullable, and never read or written. Sign-in goes through Supabase Auth, by email and password or by Google OAuth (`web/src/app/login/page.tsx`, `signup/page.tsx`), so credentials live in Supabase and no code path sets this column. Google sign-in also makes Google a source of the account's identity data. |
| `username` | **identifier** | Handle. `NULL` until the user chooses one, except for accounts migration 010 gave a generated one. A chosen handle is public; a generated one is not served (below). Lower-cased, unique by functional index. Doubles as how other users refer to them. |
| `display_name` | profile | Chosen, optional. |
| `bio` | profile | Chosen, optional, 280 chars. |
| `avatar_url` | profile | Stored as the object's public R2 URL; served presigned when R2 is configured. |
| `is_private` | operational | Defaults **true**, and governs **who may follow, not who may see**. `services/access.py` says so in bold and `test_account_privacy_does_not_clamp_post_visibility` pins it: a private account's `public` post is readable by a signed-out stranger. The column is stored and echoed; no access decision reads it. |
| `created_at`, `username_changed_at`, `username_is_generated` | operational | Account lifecycle. `username_is_generated` marks a handle migration 010 derived from the email local part. |

**One thing worth a lawyer's attention:** accounts that already existed when
migration 010 ran were given a handle derived from their email local part
(`username_is_generated`). Accounts created since have no handle until they
choose one, because `_ensure_user_exists` stores only `id` and `email`. A
generated handle is not published: `GET /users/{handle}` returns 404 for it,
and post responses null it (`PostAuthor.from_author`). It is still held, so
`GET /users/handle-available` tells a signed-in caller that the name is not
available, and the stored value is still a transformation of the email
address.

### Beyond the users table

The heavier personal data is not in `users` at all — it is **video of
identifiable people, most of them minors**. Uploaded footage, generated clips
and condensed renders live in R2 under **identifier-derived keys**, not content
hashes: `raw/{game_id}.{ext}` (`raw/{game_id}` when the upload has no
extension), `clips/{game_id}/{clip_id}.mp4`, `thumbs/{game_id}/{clip_id}.jpg`
(frames of the same footage), `condensed/{game_id}.mp4` and `avatars/{user_id}`
(`services/storage.py`). That matters here because a content-addressed key
dedupes across users, while these embed row ids and do not. The ids are random
UUIDs, so the keys are unguessable, but each one names the row it belongs to.

`players` and `teams` carry names attached to that footage — `players.name`
(required), `players.jersey_number`, `players.photo_url`. **Neither table has
any deletion path.** `routers/players.py` exposes `GET`, `POST` and `PATCH` and
no DELETE; nothing anywhere calls `db.delete()` on a `Player` or a `Team`; and
deleting a game deletes its clips but never a `players` row, so a player row
survives its last clip. `players` also carries **no foreign key to
`users.id`** — only `team_id` — so it can never appear in the deletion table
below, and the guard test cannot see it either. The one table holding what this
document calls the sensitive part is outside both.

Also user-typed and not tabulated above, because the table is scoped to `users`:
`posts.caption`, `games.title`, `collections.name`. Named here rather than left
to the reader to notice, since a scope boundary the document does not state
reads as completeness.

Nothing in this document should be read as suggesting the account columns are
the sensitive part; they are the easy part.

---

## 2. Who can see it

Visibility is `clip.visibility or game.visibility` (`api/app/services/
access.py`) — a clip overrides its game, and the default is private.

**On `main` today the reachable tier is `private`, and the reason is not a
flag.** No router writes `Game.visibility` or `Clip.visibility`
(`models/visibility.py` says so in capitals, and
`test_no_visibility_write_path.py` pins it), so `widest_allowed` resolves to
`private` for every row and `create_post` refuses anything wider. `followers`
is unreachable for a second reason as well: `is_follower` returns `False`
unconditionally until CF-110 lands, so followers-tier content is owner-only
regardless.

The flag that does exist on `main` is `SOCIAL_ENABLED`, and `render.yaml` sets
it to `"true"`, mounting `/users/*` and `/posts/*` in production. An earlier
draft of this section said a `PUBLIC_POSTING_ENABLED` setting kept public
posting off; on `main` no such setting exists.

**That changes when #481 merges, and this section has to be re-read then.**
CF-109b (#482) was merged into #481's branch, which is open against `main`. It
adds `PATCH /clips/{clip_id}/visibility`, lets `create_post` raise a clip's
tier through `raise_clip_visibility`, and replaces
`test_no_visibility_write_path.py` with
`test_visibility_write_paths_are_declared.py`. Owners can then set `followers`,
which stays owner-only while `is_follower` returns `False`, and `public` only
when a new `PUBLIC_POSTING_ENABLED` setting allows it. That setting defaults to
off, #481's `render.yaml` sets it to `"false"`, and it gates the write only:
turning it off after it has been on does not hide rows already made public.
Read this section as a countdown rather than a reassurance.

---

## 3. Deletion — the gap

**Account deletion is not implemented, and could not succeed today if it were.**

Three independent reasons, and the third is the one an implementer is most
likely to miss:

1. **There is no endpoint.** There *is* a `users` router — `routers/profiles.py`
   carries the `/users` prefix and serves `GET /me`, `GET /handle-available`,
   `PATCH /me`, `POST /me/avatar` and `GET /{handle}` — but it has no DELETE
   route, and nothing in the API removes a user row.
2. **The schema refuses it.** Three tables reference `users.id` with no
   `ON DELETE` clause, which in PostgreSQL means `NO ACTION` — the delete is
   rejected while any referencing row exists:

| Table | On delete | Effect |
| --- | --- | --- |
| `games` | *(none)* | **Blocks deletion** |
| `teams` | *(none)* | **Blocks deletion** |
| `collections` | *(none)* | **Blocks deletion** |
| `posts` | `CASCADE` | Removed with the account |
| `corrections` | `CASCADE` | Removed with the account |
| `upload_events` | `CASCADE` | Removed with the account |

Any user who has uploaded a game, made a team, or built a collection — that is,
every real user — cannot be deleted without those rows being dealt with first.

The failure is at least the *safe* one: a hard `ForeignKeyViolation`, not a
partial delete leaving orphaned footage behind. But it means **a policy must not
promise erasure on request until this is built.** That is the single most
consequential line in this document.

3. **The account does not live here.** `public.users` is a **mirror**.
   `_ensure_user_exists` inserts `id` and `email` from the verified Supabase JWT
   on the first authenticated request, and it runs on every request. So even
   with the three blockers above resolved and a `DELETE /me` shipped, deleting
   the local row erases nothing: the identity and the email remain in Supabase
   `auth.users`, and the user's next request recreates the mirror. Erasure needs
   a Supabase Auth admin delete as well — which is invisible to the table above
   and to the guard test, because it is not in `Base.metadata`.

There is a second-order question for the lawyer, not for the schema: `upload_events`
is deliberately append-only, because it is what the minute quota is counted from
and a refundable quota is an abuse vector. It cascades on user delete, so erasure
would discard the record of consumption — which is correct for privacy and a
decision someone should make knowingly rather than discover.

### What erasure would need

Not a proposal, just the shape of the work: a Supabase Auth delete alongside
the local one, a decision per table on whether rows are deleted or detached
(`ON DELETE SET NULL`, as `upload_events.game_id` already does for games), the
R2 objects behind a user's footage, a deletion path for `players` and `teams`
where none exists at all, and whether a deleted user's clips inside *other
people's* collections survive. Collections are cross-owner, so that last one is
a real question and not a detail.

**An implementer who works only the table above will build an incomplete erasure
and believe it is finished.** That is the failure this section exists to
prevent, so it is stated rather than implied.

---

## 4. What this document does not cover

- Retention periods, **except the two sweeps and the one storage rule that
  already run**, which belong here rather than in a "not covered" list:
  - `_sweep_expired_raw_uploads` clears `games.raw_video_url` and deletes the R2
    objects under `raw/` for games in `ready` or `failed` created more than
    `raw_upload_retention_days` ago — **default 7**; `0` keeps footage forever.
    It runs at the end of any successful `process_game`, and there is no cron,
    so footage is **eligible for deletion after 7 days and deleted at the next
    successful processing run**, not on day 7. While the system is idle nothing
    is deleted, and a failure is logged and swallowed. A second pass then
    deletes any object under `raw/` older than the cutoff that no row references.
  - `_sweep_abandoned_uploads` (`routers/games.py`) deletes a user's
    `uploading` game rows older than `abandoned_upload_hours` — **default 24**
    — and their upload objects, when that user next starts an upload that
    passes the quota check. It is best-effort, and reaches only users who upload
    again.
  - Outside the API, the R2 bucket's lifecycle rule aborts incomplete multipart
    uploads after 7 days (`infra/README.md`). R2 creates that rule by default;
    this document does not verify the live bucket.

  An earlier draft of this section said nothing expires anything, and a later
  one said footage is destroyed after exactly a week; a policy drafted from
  either would promise something the system does not do. Clips, thumbnails,
  condensed renders and every other row have no expiry.
- **R2 objects orphaned by a game delete.** `delete_game` removes the row first
  and then deletes the objects best-effort, swallowing per-key failures, and the
  retention sweep only walks `raw/`. A failed delete leaves clip and condensed
  objects with no row referencing them and nothing that will ever reclaim them —
  footage outliving the user's deletion of it.
- Third parties. Supabase (auth + database), Cloudflare R2 (object storage),
  Render (runs the api, the worker, the web app and the Redis key-value
  store, per `render.yaml`), **Google Fonts** (the root layout loads its
  stylesheet from `fonts.googleapis.com` on every page, so each visitor's
  browser contacts Google), **Modal** and **Sentry** (errors from the api, the
  worker and the browser; performance tracing is off unless a sample rate is
  set, since `sentry_traces_sample_rate` and the web's
  `SENTRY_TRACES_SAMPLE_RATE` variables default to 0) all process this data.
  - **Modal receives whole videos.** For GPU ball tracking the worker hands
    Modal a one-hour presigned URL to the uploaded video, and
    `ml/modal_app.py` downloads the entire file; pose refinement presigns its
    own one-hour URL to the same video, which `ml/modal_pose.py` reads in
    place, or downloads whole when the store cannot seek. The pose-first
    scan, the fallback when ball tracking is unavailable, also sends Modal a
    one-hour URL to the whole video (`_run_detection` in
    `api/app/workers/tasks.py`).
  - **Roboflow receives no footage from this code.** It supplies the ball
    model's weights, fetched with `ROBOFLOW_API_KEY` by
    `inference.get_model`, and the model runs on Modal. The worker's local
    fallback cannot run it in production: the worker image ships no
    `inference` package (`Dockerfile.api`). Whether that package reports usage
    to Roboflow is not verified here.
  Sentry is configured with `send_default_pii=False` and
  `max_request_body_size="never"`, which limits what reaches it rather than
  making it a non-processor. Enumerating the sub-processors is a separate pass,
  and it matters: whole videos of identifiable minors are sent to Modal.
  Their own retention terms are contractual facts, not code facts, and are not
  verified here.
- Anything about lawfulness, consent, or what any jurisdiction requires. Those
  are the questions CF-88 exists to answer, and they are not
  engineering questions.
