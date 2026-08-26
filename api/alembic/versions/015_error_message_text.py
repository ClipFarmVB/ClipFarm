"""Widen games.error_message to Text (CF-226)

`error_message` was varchar(1024). The value written to it is `str(exc)` from an
arbitrary failure, and a Modal remote traceback reliably exceeds that.

The overflow does not merely lose the message. `sync_set_game_status(…, "failed",
…)` runs from inside `process_game_task`'s own `except` handler, so a DataError
raised there costs the `failed` write *and* the retry decision — the `finally`
still runs and the advisory lock is released, but the row never leaves
`processing`. That is the CF-184 stranded-game symptom, reached by the code whose
job is to explain a failure. CF-225 added a clamp on the write path as a
mitigation; this is the fix, and it lets the clamp stand down on its own
(`_ERROR_MESSAGE_MAX` is read off the column, so an unbounded type makes
`_fit_error_message` a no-op with no edit).

Text rather than a larger fixed width, for the same reason as `upload_id` in 014:
no width is defensible when the input is an arbitrary traceback, and in Postgres
text and varchar(n) share storage and performance — the only thing a bound buys
here is the outage.

Revision ID: 015
Revises: 014
Create Date: 2026-08-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "games",
        "error_message",
        existing_type=sa.String(length=1024),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # This does NOT truncate — it aborts. `ALTER COLUMN … TYPE varchar(1024)`
    # with no USING clause applies an *assignment* cast, which raises
    # `value too long for type character varying(1024)` on the first row that
    # does not fit. (An explicit `::varchar(1024)` would truncate silently; the
    # implicit conversion here does not, and 014's "lossy by nature" wording is
    # imprecise about the same thing.)
    #
    # Left that way deliberately. The rows too long to fit are exactly the ones
    # this migration exists to make storable — full tracebacks — and a rollback
    # is when an operator most needs them. Failing loudly beats quietly deleting
    # the diagnostic mid-incident.
    #
    # To downgrade anyway, decide the loss explicitly first:
    #
    #     UPDATE games SET error_message = left(error_message, 1024)
    #      WHERE length(error_message) > 1024;
    #
    # then run this. The full text remains in the worker log and in Sentry.
    op.alter_column(
        "games",
        "error_message",
        existing_type=sa.Text(),
        type_=sa.String(length=1024),
        existing_nullable=True,
    )
