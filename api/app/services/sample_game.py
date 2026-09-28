"""The example game a new account starts with (CF-220).

A fresh signup used to land on an empty Library, for a product whose value
takes an upload and a processing run to show. When `SAMPLE_GAME_ID` names a
`ready` game, `copy_sample_game` gives each newly provisioned account its own
copy of that game's rows, marked `is_sample`.

**Rows are copied; media is not.** Each copied clip stores the same
`clip_url` / `thumbnail_url` strings as its source, so every copy plays the one
set of R2 objects. That is what makes a per-signup copy cheap, and it is also
why the copies need guards the owner's own games do not:

* the delete paths must not remove those objects (routers/games.py,
  routers/clips.py) — removing the example is removing rows only;
* the owner may not publish the footage (clip visibility, posts), because it
  is not theirs to publish;
* a relabel writes no `Correction`, so our own footage does not become
  training signal attributed to a stranger;
* the source game itself cannot be deleted or trimmed while the setting names
  it (`assert_not_source`).

**Left out of the copy on purpose:**

* `raw_video_url` — a trim would re-cut from the source upload and overwrite
  the SHARED clip object in place, changing every copy at once. Null makes
  trim refuse, and keeps the retention sweep away from the source's upload.
* `condensed_video_url` — a re-condense of the source replaces that object,
  and a copy pointing at it would silently change or break.
* `player_id` — the source's roster names are not the new owner's to see.
* `upload_id`, and no `upload_events` row — nothing was uploaded, so nothing
  is charged against the quota.
* `visibility` — never passed, so the copy is private like any new game.

Called only from `auth._ensure_user_exists`, inside a savepoint, on the
request that actually created the user row. It raises freely; the caller
decides that a failure here costs the example and never the signup.
"""
import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.clip import Clip
from app.models.game import Game, GameStatus

logger = logging.getLogger(__name__)

TITLE_PREFIX = "Example: "
# games.title is String(255).
_TITLE_MAX = 255


def is_sample(game: object) -> bool:
    """Whether `game` is a copy of the example game.

    `is True`, not truthiness, and through getattr: the routers that call this
    are exercised across the suite with duck-typed game stubs, some of which
    predate the column, and a MagicMock's attribute is a truthy Mock. Either
    must read as "not a sample" — the unguarded path — rather than tripping a
    refusal on an ordinary game.
    """
    return getattr(game, "is_sample", False) is True


def assert_not_sample(game: object) -> None:
    """409 for publishing the example game's footage.

    Used by `PATCH /clips/{id}/visibility`, `POST /posts` and
    `GET /clips/{id}/share`: each widens who sees a clip — the last by minting
    a presigned link that plays for anyone holding it. The example's footage
    is ours, copied into the account to show what the product does — not the
    owner's to put in front of anyone else.
    """
    if is_sample(game):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Example clips can't be shared or posted — upload your own "
                "game to publish its clips"
            ),
        )


def configured_source_id() -> uuid.UUID | None:
    """The configured source game's id, or None when off or unusable."""
    raw = settings.sample_game_id.strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        # The value, not an error object: it is our own config, not user data.
        logger.warning(
            "sample game: SAMPLE_GAME_ID is not a UUID (%r); treating the "
            "example game as off",
            raw,
        )
        return None


def assert_not_source(game_id: uuid.UUID) -> None:
    """409 for deleting or trimming the configured source game itself.

    Used by `DELETE /games/{id}`, `POST /clips/delete` and
    `PATCH /clips/{id}/trim` — the paths that delete or overwrite the R2
    objects every copy plays. Follows the *current* setting only: a source
    that `SAMPLE_GAME_ID` no longer names has no guard here, which is why the
    README says to keep a retired source untouched while copies of it exist.
    """
    source_id = configured_source_id()
    if source_id is not None and game_id == source_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is the example game every new account copies — its "
                "clips can't be deleted or trimmed while SAMPLE_GAME_ID names it"
            ),
        )


async def copy_sample_game(db: AsyncSession, user_id: uuid.UUID) -> Game | None:
    """Add a copy of the configured example game for `user_id` to `db`.

    Returns the new game, or None when there is nothing to copy — setting
    empty or malformed, source missing, or source not `ready`. Flushes but does
    not commit: the caller owns the transaction.
    """
    source_id = configured_source_id()
    if source_id is None:
        return None

    source = await db.get(Game, source_id)
    if source is None:
        logger.warning("sample game: source %s does not exist; skipping the copy", source_id)
        return None
    if source.status != GameStatus.ready:
        logger.warning(
            "sample game: source %s is %s, not ready; skipping the copy",
            source_id,
            source.status.value,
        )
        return None

    source_clips = (
        await db.execute(
            select(Clip).where(Clip.game_id == source_id).order_by(Clip.start_time)
        )
    ).scalars().all()

    # Every field by name. A generic column loop would carry across whatever
    # the next migration adds — visibility included, which
    # test_visibility_write_paths_are_declared.py exists to stop.
    copy = Game(
        owner_id=user_id,
        title=(TITLE_PREFIX + source.title)[:_TITLE_MAX],
        status=GameStatus.ready,
        is_sample=True,
        raw_video_url=None,
        upload_id=None,
        condense_requested=False,
        condensed_video_url=None,
        original_duration=source.original_duration,
        condensed_duration=source.condensed_duration,
        progress=1.0,
        processed_at=source.processed_at,
    )
    db.add(copy)
    await db.flush()

    for clip in source_clips:
        db.add(
            Clip(
                game_id=copy.id,
                player_id=None,
                action_type=clip.action_type,
                confidence=clip.confidence,
                highlight_score=clip.highlight_score,
                start_time=clip.start_time,
                end_time=clip.end_time,
                clip_url=clip.clip_url,
                thumbnail_url=clip.thumbnail_url,
                labels=list(clip.labels or []),
            )
        )
    await db.flush()

    logger.info(
        "sample game: copied %s (%d clips) to user %s as %s",
        source_id,
        len(source_clips),
        user_id,
        copy.id,
    )
    return copy
