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


# NOTHING WRITES THIS YET (CF-108 scope).
#
# No schema field, no PATCH body and no router assigns visibility, so every game
# is `private` and every clip NULL until CF-109 adds the setter along with
# posting. "Nothing becomes newly visible" is therefore true by construction
# rather than by policy — which is the safe direction, but it means the
# public/anonymous surface ships unexercised end to end.
#
# What that leaves unverified: the ORM enum round-trip, the SQL filters against
# real rows, and the anonymous HTTP paths. The 44-case matrix in
# api/tests/test_access.py is unit-level, over hand-built stand-ins. The test
# worth having is one that flips a row to `public` in the database and drives
# GET /games/{id}, GET /games/{id}/clips, GET /clips/{id}/share and
# GET /clips/{id}/download — it needs a database fixture the api suite doesn't
# have yet, so it belongs with the setter in CF-109 rather than being faked
# here.
