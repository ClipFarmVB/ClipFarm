from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

# The token column is `Text`, so this is the only bound there is — deliberately.
# A caller sending something absurd gets a 422 naming the field, rather than a
# `StringDataRightTruncationError` out of the driver or an unbounded value in a
# unique btree index (which tops out around 2704 bytes). Widening it later is a
# one-line change with no migration, which is the whole reason the bound is
# here and not on the column.
#
# **Counted in bytes, because the ceiling it has to stay under is a byte
# ceiling.** `max_length` counts characters and UTF-8 spends up to four bytes
# on one, so a 1024-*character* bound accepts 4096 bytes — past the btree
# limit, where Postgres refuses the index entry for `uq_device_tokens_token`.
# `app/main.py` registers no exception handler, so that arrives as a 500 and
# not as the readable 422 this comment promises. Every real Expo, APNs and FCM
# token is ASCII, which is why it takes a hostile or buggy client to reach it
# and why the bound was easy to write in the wrong unit.
#
# For scale: an Expo push token is ~40 characters, an APNs device token 64 hex,
# an FCM registration token ~160-200 and not documented as bounded.
MAX_TOKEN_BYTES = 1024


def _within_the_index_bound(v: str) -> str:
    encoded = len(v.encode("utf-8"))
    if encoded > MAX_TOKEN_BYTES:
        raise ValueError(
            f"token is {encoded} bytes as UTF-8; the maximum is {MAX_TOKEN_BYTES}"
        )
    return v


# Shared by both schemas: the bound belongs to the column's unique index, not to
# one route. `min_length` stays in characters, which is the honest unit for "not
# empty" — one character is at least one byte either way.
Token = Annotated[str, Field(min_length=1), AfterValidator(_within_the_index_bound)]


class DeviceTokenRegister(BaseModel):
    """Body of `POST /devices/tokens`.

    `platform` is a `Literal` rather than a free string: it is the only
    validation the value gets, since the column is a plain `String(16)` with no
    database-level check. Adding a platform means adding it here.
    """

    platform: Literal["ios", "android"]
    token: Token


class DeviceTokenUnregister(BaseModel):
    """Body of `POST /devices/tokens/unregister`.

    Token only. `platform` is not asked for and would not be used — the token
    already identifies the row, and accepting a platform here would invite a
    caller to send a mismatched pair and expect something to happen.
    """

    token: Token
