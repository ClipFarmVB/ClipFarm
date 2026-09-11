# Personal-data inventory

**This is not a privacy policy, and it is not legal advice.** It is the
engineering input to one: what personal data ClipFarm stores, where it lives,
who can see it, and what happens to it when an account goes away. CF-75 and
CF-88 need a lawyer; a lawyer needs this first, because a policy drafted
against a guess describes a system that does not exist.

Everything below is read off the schema and the routers. `api/tests/
test_personal_data_inventory.py` fails when a `users` column or a table
referencing it appears, disappears, or changes its delete behaviour without
this file being updated — so the document cannot drift from the code silently.

---

## 1. What is stored about a person

All on the `users` table unless noted.

| Column | Class | Notes |
| --- | --- | --- |
| `id` | operational | Internal UUID. Appears in no URL a stranger can guess their way to. |
| `email` | **identifier** | Unique, required. The only field that reaches a real person off-platform. |
| `hashed_password` | **credential** | Nullable — null means SSO-only, so the account has no local password at all. |
| `username` | **identifier** | Public handle. Lower-cased, unique by functional index. Doubles as how other users refer to them. |
| `display_name` | profile | Chosen, optional. |
| `bio` | profile | Chosen, optional, 280 chars. |
| `avatar_url` | profile | Stored key; served presigned when R2 is configured. |
| `is_private` | operational | Defaults **true**. The footage is youth sports, so nothing reaches a non-follower without a deliberate opt-in. |
| `created_at`, `username_changed_at`, `username_is_generated` | operational | Account lifecycle. `username_is_generated` marks a handle migration 010 derived from the email local part. |

**One thing worth a lawyer's attention:** a generated handle is derived from the
email local part. For a user who never claimed one, their public identifier is a
transformation of their email address. The frontend withholds a generated handle
from post responses, but the derivation is still the origin of the value in the
database.

### Beyond the users table

The heavier personal data is not in `users` at all — it is **video of
identifiable people, most of them minors**. Uploaded footage, generated clips
and condensed renders live in R2, keyed by content hash. `players` and `teams`
carry names attached to that footage. Nothing in this document should be read
as suggesting the account columns are the sensitive part; they are the easy
part.

---

## 2. Who can see it

Visibility is `clip.visibility or game.visibility` (`api/app/services/
access.py`) — a clip overrides its game, and the default is private. Public
posting is additionally gated behind `PUBLIC_POSTING_ENABLED`, which is **off**,
so the only sharing tier reachable today is `followers`.

That gate is the reason this inventory is not urgent-but-late: the exposure a
privacy policy most needs to describe is currently closed in code.

---

## 3. Deletion — the gap

**Account deletion is not implemented, and could not succeed today if it were.**

Two independent reasons:

1. **There is no endpoint.** No `users` router, no `DELETE /me`. Nothing in the
   API removes a user row.
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

There is a second-order question for the lawyer, not for the schema: `upload_events`
is deliberately append-only, because it is what the minute quota is counted from
and a refundable quota is an abuse vector. It cascades on user delete, so erasure
would discard the record of consumption — which is correct for privacy and a
decision someone should make knowingly rather than discover.

### What erasure would need

Not a proposal, just the shape of the work: a decision per table on whether
rows are deleted or detached (`ON DELETE SET NULL`, as `upload_events.game_id`
already does for games), the R2 objects behind a user's footage, and whether a
deleted user's clips inside *other people's* collections survive. Collections
are cross-owner, so that last one is a real question and not a detail.

---

## 4. What this document does not cover

- Retention periods. Nothing expires anything today; there is no scheduled
  deletion of footage, clips or renders.
- Third parties. Supabase (auth + database), Cloudflare R2 (object storage),
  Modal and Roboflow (inference on uploaded frames) all process this data.
  Enumerating the sub-processors is a separate pass, and it matters: frames of
  identifiable minors are sent to inference providers.
- Anything about lawfulness, consent, or what any jurisdiction requires. Those
  are the questions CF-75 and CF-88 exist to answer, and they are not
  engineering questions.
