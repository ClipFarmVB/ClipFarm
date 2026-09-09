"""Content visibility tiers (CF-108).

Shared by Game and Clip. Lives in its own module because both models import it
and putting it in either one creates a circular import.
"""
import enum


class Visibility(str, enum.Enum):
    """Who may read a piece of content.

    Ordered least → most visible. `private` is the default everywhere: this is
    youth-sports footage, so nothing becomes readable by a non-owner without a
    deliberate act (epic decision 1, CF-106).
    """

    private = "private"      # owner only
    followers = "followers"  # owner + accepted followers (CF-110)
    public = "public"        # anyone, including signed-out visitors


# WHAT WRITES THIS (was: nothing, through CF-108 and CF-109).
#
# `Clip.visibility` is written by two paths, both added by CF-109b (#398):
# `PATCH /clips/{id}/visibility`, and `POST /posts` with
# `raise_clip_visibility`. Both are owner-only, both set the CLIP and never the
# game, and both refuse `public` unless `PUBLIC_POSTING_ENABLED` is on — see
# `services/publishing.py` for why that tier is gated apart from `followers`.
#
# `Game.visibility` is still written by nothing, and that is deliberate rather
# than pending: raising a game publishes every clip in it, which is the silent
# side effect `create_post`'s 409 exists to prevent. A clip overrides its game
# rather than being bounded by it, so publishing one clip needs no game write.
#
# Until CF-109b, "nothing becomes newly visible" was true by construction — no
# write path existed at all, and `api/tests/test_no_visibility_write_path.py`
# enforced that. It is now true by policy instead, which is the weaker guarantee
# and the reason the flag exists.
#
# What that leaves unverified: the ORM enum round-trip, the SQL filters against
# real rows, and the anonymous HTTP paths. The 44-case matrix in
# api/tests/test_access.py is unit-level, over hand-built stand-ins. The test
# worth having is one that flips a row to `public` in the database and drives
# GET /games/{id}, GET /games/{id}/clips, GET /clips/{id}/share and
# GET /clips/{id}/download — it needs a database fixture the api suite doesn't
# have yet, so it belongs with the setter in CF-109 rather than being faked
# here.
