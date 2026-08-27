import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete as sa_delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_optional_user_id
from app.config import settings
from app.database import get_db
from app.models.game import Game, GameStatus
from app.models.clip import Clip
from app.schemas.game import (
    GameOut,
    GameRename,
    UploadComplete,
    UploadConfig,
    UploadCreate,
    UploadPart,
    UploadTicket,
)
from app.services import access, quota, storage
from app.workers.tasks import process_game_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/games", tags=["games"])

# CF-244: the presigned PUT binds the declared Content-Type to the signature as
# a *header*, so R2 rejects a mismatched header and accepts any bytes behind it.
# These identify the container the bytes actually are.
#
# Deliberately no new dependency, following CF-236 (#253), which sniffs avatar
# uploads the same way: python-magic pulls in a C library to recognise hundreds
# of formats when four are permitted.
#
# FAMILY, not MIME type. The four allowed types collapse into two containers —
# video/mp4 and video/quicktime are both ISO-BMFF, video/x-matroska and
# video/webm are both EBML — and separating the pairs means reading an ISO brand
# or an EBML DocType. Browsers fill File.type from the file extension, so a
# QuickTime-branded .mov routinely arrives declared video/mp4; a strict
# declared-vs-sniffed equality check would reject it. The declared allowlist in
# quota.check_upload_allowed still governs what a client may *declare*; this is a
# separate, coarser check on what it actually *sent*.
# How much of the object to read. Wider than the 8 bytes a box header needs, so
# the scan below can step over a leading box and look at the next one.
VIDEO_SIGNATURE_BYTES = 64
# The floor for "too small to be a container at all", deliberately NOT the read
# size above. Sizing the destructive reject off the read window would mean every
# widening of the window rejected more real files; this only has to sit below
# the shortest header worth sniffing.
MIN_CONTAINER_BYTES = 16
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"

# Box types that identify the ISO-BMFF/QTFF family when one of them leads the
# file.
#
# NOT just `ftyp` (CF-244 review). ISO/IEC 14496-12 requires `ftyp` early, so an
# MP4 always carries it — but `video/quicktime` is on the allowlist too, and
# QTFF does not require it: a .mov may legally begin with `wide`, `moov`,
# `mdat`, `free`, `skip` or `pnot`. Measured rather than reasoned about —
# replacing the 20-byte `ftyp` of an ffmpeg-produced .mov with a same-length
# `free` box (same length, so every chunk offset in the moov stays valid) leaves
# a file ffprobe reports as `format_name=mov,mp4,m4a,3gp,3g2,mj2` with its
# duration intact and exit 0, while an `ftyp`-only check called it not-a-video.
#
# Getting this wrong is expensive in one direction only. The reject path deletes
# the Game row and the R2 object and answers 415 with no retry, so the user
# re-transfers up to 8 GB — the same cost the fail-open decision below is
# written to avoid, except paid on a confident wrong answer instead of an
# uncertain one.
_ISO_BMFF_BOX_TYPES = frozenset({
    b"ftyp",  # ISO-BMFF brand — required early in MP4, optional in QuickTime
    b"styp",  # fragmented mp4 segment
    b"moov",  # movie header; leads a faststart / streaming-ordered file
    b"mdat",  # media data
    b"wide",  # 64-bit-size placeholder ffmpeg's mov muxer emits before mdat
    b"free",  # padding, and what stripping an atom in place leaves behind
    b"skip",  # padding, the other spelling
    b"junk",  # padding, as written by some capture tools
    b"pnot",  # preview, on old QuickTime files
})
# Enough to step over a couple of small leading boxes. The read window bounds
# the walk anyway; this just keeps it obviously finite.
_ISO_BMFF_MAX_BOXES = 4


def _sniff_video_container(header: bytes) -> str | None:
    """The container family `header` belongs to, or None if it is neither."""
    if header.startswith(_EBML_MAGIC):
        return "ebml"
    if _opens_with_iso_bmff_box(header):
        return "iso-bmff"
    return None


def _opens_with_iso_bmff_box(header: bytes) -> bool:
    """Whether `header` opens with a recognisable ISO-BMFF/QTFF box chain.

    Walks a few boxes instead of testing only the first, because the set of
    types that may lead a QuickTime file is open-ended and any fixed list will
    be narrower than the decoder's. ffmpeg does not match on the leading type at
    all — it follows the chain — which is why the same .mov with its first atom
    relabelled to a type nothing recognises still probes as
    `mov,mp4,m4a,3gp,3g2,mj2`. Stepping over an unrecognised but well-formed box
    keeps this from deleting such a file.

    What bounds the cost of that is the size field: a box declares its own
    length before its type, and only a plausible length lets the walk continue,
    so arbitrary bytes have to survive a chain of them to reach an accept.
    """
    offset = 0
    for _ in range(_ISO_BMFF_MAX_BOXES):
        # ISO-BMFF puts a 4-byte box size first, so the type is at offset 4.
        if offset + 8 > len(header):
            return False
        size = int.from_bytes(header[offset:offset + 4], "big")
        if header[offset + 4:offset + 8] in _ISO_BMFF_BOX_TYPES:
            return True
        if size == 1:
            # `1` means the real, 64-bit size follows the type.
            if offset + 16 > len(header):
                return False
            size = int.from_bytes(header[offset + 8:offset + 16], "big")
        if size < 8:
            # `0` means "runs to the end of the file", legal only for the last
            # box — so there is nothing after it to step to. 2..7 cannot hold a
            # box header at all, so these are not boxes.
            return False
        offset += size
    return False


DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
# Read paths use this: None when signed out, so public content stays
# reachable without an account (CF-108).
ViewerId = Annotated[uuid.UUID | None, Depends(get_optional_user_id)]


@router.get("", response_model=list[GameOut])
async def list_games(user_id: UserId, db: DB):
    # `uploading` rows are excluded: the browser is still PUTting the video to
    # R2 and there is nothing to show or open yet. A row only becomes a game in
    # the Library once the object is confirmed (see complete_upload).
    result = await db.execute(
        select(Game)
        .where(Game.owner_id == user_id, Game.status != GameStatus.uploading)
        .order_by(Game.created_at.desc())
    )
    games = result.scalars().all()

    # Attach clip counts
    clip_counts_q = await db.execute(
        select(Clip.game_id, func.count(Clip.id).label("n"))
        .where(Clip.game_id.in_([g.id for g in games]))
        .group_by(Clip.game_id)
    )
    counts = {row.game_id: row.n for row in clip_counts_q}

    out = []
    for g in games:
        d = GameOut.model_validate(g)
        d.clip_count = counts.get(g.id, 0)
        out.append(d)
    return out


# ── Presigned direct-to-R2 uploads (CF-163) ─────────────────────────────────
# The api issues an upload ticket and later confirms the object; the video
# itself goes browser → R2 and never passes through this process.
#
# NOTE: these static paths must stay ABOVE `GET /{game_id}` — FastAPI matches
# in declaration order, so "/games/upload-config" would otherwise be parsed as
# a game_id and 422 on the UUID conversion.


def _sanitize_filename(filename: str) -> str:
    """Strip path separators so a crafted filename can't escape the key prefix."""
    return (filename or "upload.mp4").replace("/", "_").replace("\\", "_").replace("..", "_")


async def _sweep_abandoned_uploads(user_id: uuid.UUID, db: AsyncSession) -> None:
    """
    Drop this user's upload tickets that were never completed.

    A closed tab or a failed transfer leaves an `uploading` row and, for a
    multipart upload, parts that R2 bills for until they are aborted. Sweeping
    on the owner's next presign keeps that bounded without a scheduler — the
    only user who accumulates stale tickets is one who is actively uploading.

    Best-effort by construction: a failure here must never block a new upload.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.abandoned_upload_hours)
    try:
        result = await db.execute(
            select(Game).where(
                Game.owner_id == user_id,
                Game.status == GameStatus.uploading,
                Game.created_at < cutoff,
            )
        )
        stale = result.scalars().all()
        for game in stale:
            key = urlparse(game.raw_video_url or "").path.lstrip("/")
            # Both, not either. `upload_id` set does NOT imply the object
            # doesn't exist: complete_multipart runs before the row is claimed,
            # so a request that dies between assembly and the claim leaves a
            # real object on a row that still carries an upload id. Aborting
            # and then deleting covers that as well as the plain cases —
            # delete_object on a missing key is a no-op, and aborting an
            # already-completed upload just warns.
            if key and game.upload_id:
                try:
                    await run_in_threadpool(storage.abort_multipart, key, game.upload_id)
                except Exception:
                    logger.warning(
                        "Abort of abandoned multipart upload failed for game %s",
                        game.id, exc_info=True,
                    )
            if key:
                # This row is the only record of the key. Deleting it without
                # the object would strand the object unreferenced and billed.
                try:
                    await run_in_threadpool(storage.delete_file, key)
                except Exception:
                    logger.warning(
                        "Cleanup of abandoned upload object failed for game %s",
                        game.id, exc_info=True,
                    )
            await db.delete(game)
        if stale:
            await db.commit()
            logger.info("Swept %d abandoned upload(s) for user %s", len(stale), user_id)
    except Exception:
        logger.warning("Abandoned-upload sweep failed for user %s", user_id, exc_info=True)
        await db.rollback()


@router.get("/upload-config", response_model=UploadConfig)
async def upload_config(user_id: UserId, db: DB) -> UploadConfig:
    """
    The limits the client must respect, plus how much of the quota is left.

    Served rather than hardcoded in the web app so the advertised cap can never
    drift from the enforced one.
    """
    return UploadConfig.from_status(
        await quota.get_quota_status(db, user_id),
        single_put_max_bytes=settings.single_put_max_bytes,
        part_size_bytes=settings.upload_part_size_bytes,
        url_ttl_seconds=settings.upload_url_ttl_seconds,
    )


@router.post("/uploads", response_model=UploadTicket, status_code=status.HTTP_201_CREATED)
async def create_upload(body: UploadCreate, user_id: UserId, db: DB) -> UploadTicket:
    """
    Validate an intended upload and hand back presigned URLs for it.

    Every check here happens before a single byte moves. The content type is
    signed into the URL, so R2 rejects a PUT whose Content-Type *header* differs
    — but that binds the header, not the bytes (CF-244), so the stored object is
    sniffed in complete_upload. The declared size is checked against the cap and
    re-verified against the real object there too, because a presigned PUT
    cannot enforce Content-Length.

    The quota check (CF-91) is a **preview**, not the claim: it exists so a user
    who is already over their limit finds out now rather than after transferring
    2 GB. The binding reservation is taken in complete_upload, at the moment the
    upload turns into queued work — reserving here would charge for tickets that
    are abandoned, and `_sweep_abandoned_uploads` deleting those rows would then
    have to refund, which is exactly the loop the ledger exists to prevent.
    """
    content_type = body.content_type.split(";")[0].strip().lower()
    status_ = await quota.get_quota_status(db, user_id)
    rejection = quota.check_upload_allowed(
        status_,
        content_type=content_type,
        size_bytes=body.size_bytes,
        duration_seconds=body.duration_seconds,
    )
    if rejection:
        code, message = rejection
        raise HTTPException(status_code=code, detail=message)

    await _sweep_abandoned_uploads(user_id, db)

    game_id = uuid.uuid4()
    key = storage.game_raw_key(game_id, _sanitize_filename(body.filename))
    ttl = settings.upload_url_ttl_seconds

    mode: Literal["single", "multipart"]
    upload_url: str | None = None
    upload_id: str | None = None
    part_size: int | None = None
    parts: list[UploadPart] = []

    try:
        if body.size_bytes <= settings.single_put_max_bytes:
            mode = "single"
            # Pure local signing, no network — safe to call on the event loop.
            upload_url = storage.presign_put(key, content_type, ttl)
        else:
            mode = "multipart"
            part_size = settings.upload_part_size_bytes
            upload_id = await run_in_threadpool(storage.create_multipart, key, content_type)
            parts = [
                UploadPart(
                    part_number=n,
                    url=storage.presign_upload_part(key, upload_id, n, ttl),
                )
                for n in range(1, storage.plan_part_count(body.size_bytes, part_size) + 1)
            ]
    except Exception:
        logger.exception("Could not presign upload for game %s", game_id)
        raise HTTPException(status_code=502, detail="Could not start upload")

    game = Game(
        id=game_id,
        owner_id=user_id,
        title=body.title,
        status=GameStatus.uploading,
        raw_video_url=f"{settings.r2_public_url}/{key}",
        condense_requested=body.condense,
        upload_id=upload_id,
        declared_duration=body.duration_seconds,
    )
    try:
        db.add(game)
        await db.commit()
    except Exception:
        # The row is the only handle on the multipart upload — delete_game and
        # the sweep both find it through the row. If the insert fails, nothing
        # would ever be able to abort it, and its parts bill indefinitely.
        logger.exception("Could not record upload for game %s", game_id)
        await db.rollback()
        if upload_id:
            try:
                await run_in_threadpool(storage.abort_multipart, key, upload_id)
            except Exception:
                logger.warning(
                    "Abort of unrecorded multipart upload failed for %s", key, exc_info=True
                )
        raise HTTPException(status_code=500, detail="Could not start upload")

    return UploadTicket(
        game_id=game_id,
        mode=mode,
        content_type=content_type,
        expires_in=ttl,
        upload_url=upload_url,
        upload_id=upload_id,
        part_size_bytes=part_size,
        parts=parts,
    )


@router.post("/{game_id}/uploads/complete", response_model=GameOut)
async def complete_upload(
    game_id: uuid.UUID, body: UploadComplete, user_id: UserId, db: DB
) -> GameOut:
    """
    Confirm the object landed in R2, then queue processing.

    Enqueueing happens here rather than at presign time so a transfer that
    fails, is abandoned, or lies about its size never becomes a job — the
    worker only ever sees games whose video is known to exist.
    """
    game = await db.get(Game, game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Game not found")
    if game.status != GameStatus.uploading:
        # Fast path for the obvious repeat. This check alone is NOT what makes
        # completion single-shot — see the conditional UPDATE below.
        raise HTTPException(status_code=409, detail="This upload is already complete")

    key = urlparse(game.raw_video_url or "").path.lstrip("/")
    if not key:
        raise HTTPException(status_code=500, detail="Upload is missing its storage key")

    if game.upload_id:
        if not body.parts:
            raise HTTPException(
                status_code=400, detail="A multipart upload must report its parts"
            )
        try:
            await run_in_threadpool(
                storage.complete_multipart,
                key,
                game.upload_id,
                [{"PartNumber": p.part_number, "ETag": p.etag} for p in body.parts],
            )
        except Exception:
            logger.exception("Multipart completion failed for game %s", game_id)
            raise HTTPException(
                status_code=400, detail="Could not assemble the uploaded parts"
            )

    head = await run_in_threadpool(storage.head_object, key)
    if head is None:
        raise HTTPException(status_code=400, detail="No uploaded video found for this game")

    if head["size"] > settings.max_upload_bytes:
        # The declared size passed the cap at presign but the real object did
        # not. Drop both the object and the row so nothing is left to process.
        logger.warning(
            "Upload for game %s exceeded the cap (%d bytes) — discarding",
            game_id, head["size"],
        )
        try:
            await run_in_threadpool(storage.delete_file, key)
        except Exception:
            logger.warning("Cleanup of oversize upload failed for %s", key, exc_info=True)
        await db.delete(game)
        await db.commit()
        raise HTTPException(
            status_code=413,
            detail=(
                f"File is {quota.fmt_size(head['size'])}; the maximum is "
                f"{quota.fmt_size(settings.max_upload_bytes)}."
            ),
        )

    # CF-244: the only look at what was actually stored. The signature bound the
    # declared type to the PUT's Content-Type *header*, so a client that sends
    # the header it signed can put anything behind it.
    #
    # Fails OPEN on an unreadable probe, which is the opposite of what the HEAD
    # above does with its None — deliberately. A None from head_object means
    # there is no object, so there is nothing to accept and 400 is the only
    # answer. A None here means R2 would not hand over the first few bytes of an
    # object we have already seen: the transfer completed, possibly 8 GB of it,
    # and discarding it over a storage hiccup costs the user far more than
    # letting a mislabelled file through to a worker that will fail to decode it
    # anyway. The cheaper wrong answer differs between the two, so the direction
    # does.
    # Decided from the size we already have rather than from the read below.
    # An object too short to carry a container header is definitively not a
    # video, and a ranged read is the wrong instrument for it: a ranged read of
    # a zero-length object is unsatisfiable, so R2 answers 416, head_bytes
    # returns None, and the fail-open path waves it through. Reading the size
    # keeps the verdict independent of how storage chooses to answer that
    # request — and saves the request entirely.
    #
    # MIN_CONTAINER_BYTES, not VIDEO_SIGNATURE_BYTES: the window is sized for
    # how far the sniff may need to walk, and this is the destructive branch, so
    # widening the read must not widen what gets deleted (CF-244 review). A
    # short object is read and sniffed on whatever bytes exist.
    too_short = head["size"] < MIN_CONTAINER_BYTES
    header = None if too_short else await run_in_threadpool(
        storage.head_bytes, key, VIDEO_SIGNATURE_BYTES
    )
    if too_short or (header is not None and _sniff_video_container(header) is None):
        logger.warning(
            "Upload for game %s is not a video container — discarding", game_id
        )
        # The conditional delete, not the unguarded one the oversize path above
        # uses. A sibling completion may have claimed this game while we were in
        # the HEAD, and the row's own `uploading` state is what makes exactly one
        # caller able to dispose of it — the same reason the quota rejection
        # below is written this way. The verdict itself is deterministic per
        # object, so both callers would reject; the guard costs nothing and
        # keeps the two rejection paths from disagreeing about concurrency.
        discarded = await db.execute(
            sa_delete(Game).where(Game.id == game_id, Game.status == GameStatus.uploading)
        )
        if discarded.rowcount != 1:
            await db.rollback()
            raise HTTPException(status_code=409, detail="This upload is already complete")
        await db.commit()
        try:
            await run_in_threadpool(storage.delete_file, key)
        except Exception:
            logger.warning("Cleanup of non-video upload failed for %s", key, exc_info=True)
        raise HTTPException(
            status_code=415,
            detail=(
                "The uploaded file is not a video. Allowed formats: "
                f"{', '.join(sorted(settings.allowed_content_types_set))}."
            ),
        )
    if header is None and not too_short:
        logger.warning(
            "Could not read the header of game %s to check its container — "
            "allowing the upload through", game_id,
        )

    # Charge the quota (CF-91). This is the binding claim, taken here rather
    # than at presign because this is the point the upload becomes queued work:
    # an abandoned ticket never reaches it, so it is never charged, and the
    # sweep never has to refund. The check and the write are one transaction
    # serialised per user, so parallel completions cannot all read the same
    # "under the cap".
    #
    # The size charged is the real object's, not the declared one. The duration
    # is still a claim — nothing here decodes the video — but it is the claim
    # made *before* the transfer, and the worker settles it against the probe.
    # Read what we need off the row before reserving: reserve_upload commits,
    # which expires `game`, and a lazy reload on attribute access is not safe
    # on the async session.
    declared_duration = game.declared_duration
    raw_url = game.raw_video_url
    condense = game.condense_requested
    reservation, rejection = await quota.reserve_upload(
        db,
        user_id,
        # Keyed to the game, so a double-submit shares one charge instead of
        # the second call being rejected on the slot the first just took.
        game_id=game_id,
        # Not re-derived from the HEAD, and the reason is not the one that used
        # to be written here (CF-244). R2 enforced the signed *header*, so the
        # stored ContentType is necessarily the value this api already validated
        # at presign — re-checking it would compare the allowlist against itself.
        # The one case where it differs is R2 omitting the header, which would be
        # a false rejection. The bytes are what get checked, above.
        content_type=None,
        size_bytes=head["size"],
        duration_seconds=declared_duration,
    )
    if rejection or reservation is None:
        code, message = rejection or (429, "Upload quota reached.")
        # Discard the upload — but only if this request still owns it. A
        # sibling completion of the same game may have claimed it while we were
        # in the HEAD, and deleting the object and row then would destroy the
        # upload that request is about to enqueue. Making the row's own
        # `uploading` state the guard means exactly one caller can dispose of
        # it, for the same reason the claim below is a conditional UPDATE.
        discarded = await db.execute(
            sa_delete(Game).where(Game.id == game_id, Game.status == GameStatus.uploading)
        )
        if discarded.rowcount != 1:
            await db.rollback()
            raise HTTPException(status_code=409, detail="This upload is already complete")
        await db.commit()
        logger.info("Upload for game %s rejected by quota — discarded", game_id)
        try:
            await run_in_threadpool(storage.delete_file, key)
        except Exception:
            logger.warning("Cleanup of over-quota upload failed for %s", key, exc_info=True)
        raise HTTPException(status_code=code, detail=message)

    # Claim the upload atomically. The status check above is a read-then-write:
    # a HEAD (and, for multipart, an assembly call) sit between it and this
    # point, so two completion calls — a client retry on a slow response, a
    # double-click through a stalled request — can both pass it while the row
    # still reads `uploading`, and both enqueue. Making the transition itself
    # the guard means exactly one of them affects a row, and only that one
    # queues the pipeline.
    claimed = await db.execute(
        update(Game)
        .where(Game.id == game_id, Game.status == GameStatus.uploading)
        .values(status=GameStatus.queued, upload_id=None)
    )
    if claimed.rowcount != 1:
        # A concurrent completion of this same game won. Nothing to hand back:
        # the reservation is keyed to the game, so it is the same row the
        # winner is using, and deleting it here would uncharge their upload.
        await db.rollback()
        raise HTTPException(status_code=409, detail="This upload is already complete")

    await db.commit()
    await db.refresh(game)

    process_game_task.delay(str(game_id), raw_url, condense=condense)

    return GameOut.model_validate(game)


@router.get("/{game_id}", response_model=GameOut)
async def get_game(game_id: uuid.UUID, db: DB, viewer_id: ViewerId = None):
    # Read path: visibility-scoped, not owner-only (CF-108). viewer_id is None
    # for a signed-out visitor, which access.py resolves to "public only".
    game = access.assert_can_view_game(viewer_id, await db.get(Game, game_id))
    clip_count_q = await db.execute(
        select(func.count(Clip.id)).where(Clip.game_id == game_id)
    )
    count = clip_count_q.scalar_one()
    out = GameOut.model_validate(game)
    out.clip_count = count
    if out.condensed_video_url:
        out.condensed_video_url = storage.presign_from_stored_url(
            out.condensed_video_url,
            download_filename=f"{game.title} (condensed).mp4",
        )
    return out


@router.patch("/{game_id}", response_model=GameOut)
async def rename_game(game_id: uuid.UUID, body: GameRename, user_id: UserId, db: DB):
    game = await db.get(Game, game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Game not found")
    game.title = body.title
    await db.commit()
    await db.refresh(game)
    return GameOut.model_validate(game)


@router.delete("/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_game(game_id: uuid.UUID, user_id: UserId, db: DB):
    """Delete a game, its clips, and all associated R2 files."""
    game = await db.get(Game, game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Game not found")

    # Collect all R2 keys to delete (clips + thumbnails + raw video)
    clips_result = await db.execute(select(Clip).where(Clip.game_id == game_id))
    clips = clips_result.scalars().all()

    r2_keys: list[str] = []
    for clip in clips:
        for url in (clip.clip_url, clip.thumbnail_url):
            if url:
                r2_keys.append(urlparse(url).path.lstrip("/"))
    for url in (game.raw_video_url, game.condensed_video_url):
        if url:
            r2_keys.append(urlparse(url).path.lstrip("/"))

    # A delete during an in-flight upload: abort the multipart so its uploaded
    # parts stop being billed. Parts are invisible to delete_object — only an
    # abort (or the bucket lifecycle rule) reclaims them.
    if game.upload_id and game.raw_video_url:
        raw_key = urlparse(game.raw_video_url).path.lstrip("/")
        try:
            await run_in_threadpool(storage.abort_multipart, raw_key, game.upload_id)
        except Exception:
            logger.warning(
                "Multipart abort failed for game %s", game_id, exc_info=True
            )

    # Delete from DB (cascades to clips via relationship)
    await db.delete(game)
    await db.commit()

    # Best-effort R2 cleanup
    for key in r2_keys:
        try:
            if key:
                storage.delete_file(key)
        except Exception:
            logger.warning("R2 delete failed for key %s", key, exc_info=True)
