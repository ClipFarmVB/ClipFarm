"""Keyset cursors, in one place (CF-111 review).

Three endpoints page the same way — follower lists, following lists, pending
requests (CF-110) and the home feed (CF-111) — and each had written out its own
encode/decode pair. They were *character-identical* apart from a local variable
name, and the drift was already showing: the feed's decoder shipped without the
timezone check that `follows`' had, so one of the two silently paged from the
wrong instant. The PR that fixed it added a test whose entire job was to compare
the two functions for drift, which is the point at which there should be one
function.

CF-112 and CF-114 would have made it four copies.

**The `+1` over-fetch lives here too**, with `split_page`. It was a contract
split across two functions: a query builder that did `.limit(limit + 1)` and a
handler that did `len(rows) > limit`, with nothing in either signature saying
so. A second caller reading `.limit(21)` renders 21 cards. Keeping the two
halves in one module is what makes the extra row explainable in one place.
"""
import base64
import binascii
import uuid
from datetime import datetime
from typing import Sequence, TypeVar

from fastapi import HTTPException

T = TypeVar("T")


def encode(created_at: datetime, row_id: uuid.UUID) -> str:
    """`(created_at, id)` → an opaque token.

    The key is the **full** sort key. `created_at` alone is not unique, and
    naming `id` as the tiebreaker in an ORDER BY is an admission that ties
    happen: a cursor filtering on the timestamp alone drops the rest of a tied
    group whenever a page boundary lands inside one. Bulk-accepting a backlog of
    follow requests produces exactly that — many rows sharing a timestamp.

    Base64 so it reads as an opaque token rather than an invitation to
    hand-craft one. It is not a security boundary: every query behind it is
    visibility-filtered regardless of what a caller puts here.
    """
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Opaque token back to the sort key, or 400.

    The timestamp must carry an offset, and the reason is not the obvious one.
    asyncpg does **not** raise when a naive datetime meets a `timestamptz`
    column — measured, it returns the same rows as the aware equivalent, because
    it silently reinterprets naive input as UTC. So the failure mode is not a
    500, it is a page that starts at the wrong instant whenever the client meant
    a local time, with no error anywhere.

    A cursor this module issued is always aware. A naive one is therefore
    hand-crafted and malformed, and 400 is the honest answer.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, _, row_id = raw.partition("|")
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        return parsed, uuid.UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        # `from None`: a malformed cursor is ordinary client error, and chaining
        # the base64/ValueError traceback onto every one of them puts noise in
        # Sentry for something that is not a fault of ours.
        raise HTTPException(status_code=400, detail="Invalid cursor") from None


def split_page(rows: Sequence[T], limit: int) -> tuple[list[T], bool]:
    """`(page, has_more)` from a `limit + 1` fetch.

    The extra row is how the last page is known without a second COUNT over the
    same filtered set. `has_more` has to be read *before* the slice — computing
    it from the already-truncated list makes it permanently False, so
    `next_cursor` is always null and nothing past the first page is ever
    reachable. That ordering is the whole reason this is a function rather than
    two lines repeated at each call site.
    """
    return list(rows[:limit]), len(rows) > limit
