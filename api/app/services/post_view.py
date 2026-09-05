"""Rendering a post for the wire, in one place (CF-111 review).

`routers/posts.py` and `routers/feed.py` each had a serializer, verbatim copies
of one another down to the comment. That is the most expensive kind of
duplication here, because the two are not equally exercised: the posts one is
driven by tests, the feed one was reached by nothing at all, so every defect
below lived only in the copy nobody ran.

All three of these were real in the feed's copy and absent from the posts one,
or the reverse:

* the author was validated straight off the ORM row, which the schema's own
  docstring forbids — `from_author` exists to null a **generated** handle, and
  bypassing it published the email-derived backfill handles at feed scale;
* the author's avatar was never presigned, so a page of cards rendered a page of
  broken images behind a 200;
* one unsigned-able row took the whole page down with a 500.

And `viewer_has_liked` is the one CF-113 is about to fill in exactly one of the
two, leaving the other quietly returning a wrong value forever.
"""
import logging

from app.models.clip import Clip
from app.models.post import Post
from app.schemas.post import PostAuthor, PostOut, PostPlayback
from app.services import profiles, storage

logger = logging.getLogger(__name__)


def _playback(
    clip: Clip, *, r2_ready: bool, failures: list[str] | None = None
) -> PostPlayback:
    """Resolve playback from the clip at read time.

    Resolved per request rather than stored on the post so a trim (CF-52) or a
    re-materialized file is reflected without touching post rows.

    **A presigned URL outlives the revocation the read path enforces.** The
    signature is valid for an hour and is bearer authority on the object: once
    handed out, deleting the post, deleting the clip, or narrowing either one's
    visibility stops the *API* serving it and does nothing to the URL. So
    "delete the clip and the post 404s" is a true statement about this service
    and not about the footage, and the honest window is up to an hour.

    Not shortened here, because the tradeoff is real in both directions: the
    feed holds a page of these across a scroll session, so a short expiry trades
    a narrower revocation window for playback that dies mid-scroll — and
    re-presigning on failure just moves the same problem. The actual fix is a
    stable URL through an endpoint that re-checks visibility per request, with
    the object private. That is a storage change, not a posts one. Recorded so
    whoever writes the takedown path (CF-116) knows the guarantee they inherit
    is "within the hour".

    `r2_ready` is passed in rather than probed here: it is process-wide config
    that cannot change mid-page, and probing it per row re-read five settings
    fields per card for an answer that was already known.
    """
    if not r2_ready:
        return PostPlayback(
            clip_url=clip.clip_url,
            thumbnail_url=clip.thumbnail_url,
            proxy_url=None,
            start_time=clip.start_time,
            end_time=clip.end_time,
        )

    # Guarded per URL. A page is up to 40 signings, and an unguarded one turns a
    # single malformed `clip_url` — a stored value that never matched
    # `r2_public_url`, say, after a bucket rename — into a 500 for the whole
    # page rather than one card with no video. `profiles.serialize` has wrapped
    # the identical call since CF-107 for the same reason; the feed's blast
    # radius is 40x a profile's, and it is the default screen.
    return PostPlayback(
        clip_url=_presign(clip.clip_url, failures),
        thumbnail_url=_presign(clip.thumbnail_url, failures),
        proxy_url=None,  # CF-48 populates this
        start_time=clip.start_time,
        end_time=clip.end_time,
    )


def _presign(stored_url: str | None, failures: list[str] | None = None) -> str | None:
    if not stored_url:
        return None
    try:
        return storage.presign_from_stored_url(stored_url, expires_in=3600)
    except Exception:
        # Degrade to the stored form: unusable in a browser, but the card still
        # renders with its caption, author and counts. Never swallowed silently.
        #
        # A page renderer passes `failures` and reports once at the end. A
        # misconfigured bucket fails every URL it touches, so logging here with
        # a traceback apiece meant up to 40 stack traces per feed page per
        # request — the signal buried in its own volume. Single-post callers
        # pass nothing and keep the traceback, where one is one.
        if failures is None:
            logger.warning("Could not presign %s", stored_url, exc_info=True)
        else:
            failures.append(stored_url)
        return stored_url


def _avatar(
    url: str | None, *, r2_ready: bool, cache: dict[str, str | None] | None
) -> str | None:
    """Sign an avatar at most once per page."""
    if url is None or cache is None:
        return profiles.presign_avatar(url, r2_ready=r2_ready)
    if url not in cache:
        cache[url] = profiles.presign_avatar(url, r2_ready=r2_ready)
    return cache[url]


def serialize(
    post: Post,
    clip: Clip,
    author: object,
    *,
    r2_ready: bool,
    viewer_has_liked: bool = False,
    avatar_cache: dict[str, str | None] | None = None,
    failures: list[str] | None = None,
) -> PostOut:
    """One post, rendered.

    `author` is typed loosely because `PostAuthor.from_author` takes a Protocol
    rather than `User` — a concrete import would run the model-layer cycle the
    other way. `from_author`, never `model_validate`: the classmethod is what
    withholds a handle its owner never chose.

    `viewer_has_liked` defaults False until CF-113. It is a parameter rather
    than a hardcoded literal so that when CF-113 resolves it with one query for
    the whole page, there is one call site to thread it through instead of two
    that have to be found.

    `avatar_cache` is per page, keyed by the stored URL. A profile grid is many
    posts by *one* author, so without it a 50-post page signed the same string
    fifty times — fifty SigV4 HMACs for one answer. The feed has the milder
    version whenever an account owns several posts on a page. Optional so a
    single-post caller passes nothing.
    """
    rendered = PostAuthor.from_author(author)  # type: ignore[arg-type]
    return PostOut(
        id=post.id,
        clip_id=post.clip_id,
        caption=post.caption,
        visibility=post.visibility,
        like_count=post.like_count,
        comment_count=post.comment_count,
        created_at=post.created_at,
        author=rendered.model_copy(
            # The avatar is stored in `{r2_public_url}/{key}` form and the
            # bucket is not public, so an unsigned value is a URL the client
            # cannot load while the API reports 200. `profiles.serialize` has
            # done this since CF-107 and its docstring anticipated the feed
            # doing the same; `PostAuthor` is not a `ProfileOut`, so it could
            # not simply be handed to that function.
            update={
                "avatar_url": _avatar(
                    rendered.avatar_url, r2_ready=r2_ready, cache=avatar_cache
                )
            }
        ),
        playback=_playback(clip, r2_ready=r2_ready, failures=failures),
        viewer_has_liked=viewer_has_liked,
    )
