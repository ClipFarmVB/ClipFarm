from typing import Literal

from pydantic import BaseModel, Field

# The token column is `Text`, so this is the only bound there is — deliberately.
# A caller sending something absurd gets a 422 naming the field, rather than a
# `StringDataRightTruncationError` out of the driver or an unbounded value in a
# unique btree index (which tops out around 2704 bytes). Widening it later is a
# one-line change with no migration, which is the whole reason the bound is
# here and not on the column.
#
# For scale: an Expo push token is ~40 characters, an APNs device token 64 hex,
# an FCM registration token ~160-200 and not documented as bounded.
MAX_TOKEN_LENGTH = 1024


class DeviceTokenRegister(BaseModel):
    """Body of `POST /devices/tokens`.

    `platform` is a `Literal` rather than a free string: it is the only
    validation the value gets, since the column is a plain `String(16)` with no
    database-level check. Adding a platform means adding it here.
    """

    platform: Literal["ios", "android"]
    token: str = Field(min_length=1, max_length=MAX_TOKEN_LENGTH)


class DeviceTokenUnregister(BaseModel):
    """Body of `POST /devices/tokens/unregister`.

    Token only. `platform` is not asked for and would not be used — the token
    already identifies the row, and accepting a platform here would invite a
    caller to send a mismatched pair and expect something to happen.
    """

    token: str = Field(min_length=1, max_length=MAX_TOKEN_LENGTH)
