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
