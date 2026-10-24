"""What an owner is allowed to publish, and how wide (CF-109b, #398).

Two routers need the same two rules — `PATCH /clips/{id}/visibility` and
`POST /posts` with `raise_clip_visibility` — and neither owns them, which is
the criterion `services/access.py` states for a write-side helper with more
than one caller.

`access.py` answers "may this viewer read this?" and says in its own docstring
that writes are not its business. This is the write side of the same ladder:
which tier an owner may *set*, as opposed to which tier a viewer may read.

**Why `public` is gated separately from `followers`.**

Until this card there was no write path for a clip's or a game's visibility at
all, so nothing user-generated could be public — that sentence is what
`render.yaml` cites for running the social surface with CF-116 (abuse and
moderation) still open, and what `test_no_visibility_write_path.py` enforced
until this card replaced it with `test_visibility_write_paths_are_declared.py`
— same scan over `app/`, narrowed from "nobody may write this" to "only these
two functions may".

Those are not one exposure but two:

* **`followers`** publishes to an audience the owner approves one by one, so it
  needs no moderation surface: there is no unknown audience. **At this head
  that audience is empty.** `access.is_follower()` is False for everyone until
  CF-110 (#140) builds the follow graph, and the home feed that would read the
  tier is CF-111 (#141); both are open. Until they land, a `followers` clip or
  post is readable by its owner alone.
* **`public`** publishes youth-sports footage to signed-out strangers and to
  anything that crawls a link. That is the tier that wants terms of service
  (CF-75/CF-88, not on main), a report path and a takedown path (CF-116, open),
  and it is what turns the anonymous read endpoints from "always 404" into
  "serves real footage".

So `followers` ships on, and `public` waits behind ``PUBLIC_POSTING_ENABLED``.
Read the consequence plainly: with the flag off, every tier this API accepts is
owner-only in practice until CF-110 and CF-111 land. This card builds the write
path; it does not by itself let anyone publish to another person. Turning the
flag on is what would.

**The flag gates writes, not reads.** ``assert_tier_allowed`` below is called
from the two write paths, and nothing else consults the setting. So it decides
which tier an owner may *set*, and turning it off withdraws nothing that is
already `public`: those clips and posts stay served until their stored
visibility is narrowed, by each owner or by an operator updating the rows. A
deployment that has ever had it on cannot un-publish by flipping it back.

That is one environment variable, not a code change, so the day the terms and
the moderation path land the flag is the whole of the deploy. Both tiers are
implemented and both are tested; the flag decides which the API accepts.

This is a deliberate product decision recorded in code rather than a technical
limit — say so if you change it, rather than reading the flag as a stub.
"""
from fastapi import HTTPException, status

from app.config import settings
from app.models.visibility import Visibility


def public_posting_allowed() -> bool:
    return settings.public_posting_enabled


def assert_tier_allowed(tier: Visibility) -> None:
    """Refuse a tier the deployment has not turned on.

    422 rather than 403: the request is well-formed and the caller is
    authorized — the *value* is one this deployment does not accept, which is
    the distinction 422 carries. A 403 would read as "not your clip" and send
    the owner looking for a permission they already have.
    """
    if tier is Visibility.public and not public_posting_allowed():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Public posting is turned off on this deployment. You can share "
                "with your followers instead."
            ),
        )
