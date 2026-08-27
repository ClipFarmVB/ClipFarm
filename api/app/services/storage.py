"""Cloudflare R2 (or AWS S3) object storage helpers."""
import re
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.config import Config

from app.config import settings


class LimitedReader:
    """
    Wraps a file-like object and raises ValueError if more than `limit` bytes
    are read. Passed directly to upload_fileobj to enforce size limits even
    when the client omits a Content-Length header.

    CF-163 took video off this path (the browser PUTs straight to R2 now), but
    avatar uploads still stream through the api — they are small enough that
    presigning would be more machinery than it saves.
    """

    def __init__(self, fileobj, limit: int) -> None:
        self._f = fileobj
        self._limit = limit
        self._total = 0

    def read(self, n: int = -1) -> bytes:
        chunk = self._f.read(n)
        self._total += len(chunk)
        if self._total > self._limit:
            raise ValueError(
                f"Upload exceeds maximum size of {self._limit // (1024 * 1024)} MB"
            )
        return chunk


def r2_configured() -> bool:
    """True only when real R2 credentials are present (not placeholder values)."""
    return bool(
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and not settings.r2_account_id.startswith("your-")
        and not settings.r2_access_key_id.startswith("your-")
    )


# Socket timeouts so a dead connection fails and retries instead of hanging
# the worker forever (solo pool = one hung transfer blocks the whole queue).
# read_timeout is per-read inactivity, not total transfer time — large video
# uploads/downloads are unaffected as long as bytes keep flowing.
_BOTO_CONFIG = Config(
    signature_version="s3v4",
    connect_timeout=30,
    read_timeout=120,
    retries={"max_attempts": 3, "mode": "standard"},
)


def _client():
    endpoint = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=_BOTO_CONFIG,
        region_name="auto",
    )


def upload_file(local_path: str | Path, key: str, content_type: str = "application/octet-stream") -> str:
    """Upload a local file to R2 and return its public URL."""
    _client().upload_file(
        str(local_path),
        settings.r2_bucket_name,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return f"{settings.r2_public_url}/{key}"


def upload_fileobj(fileobj, key: str, content_type: str = "application/octet-stream") -> str:
    """Upload a file-like object to R2 and return its public URL."""
    _client().upload_fileobj(
        fileobj,
        settings.r2_bucket_name,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return f"{settings.r2_public_url}/{key}"


def download_file(key: str, local_path: str | Path) -> None:
    """Download a file from R2 to a local path using S3 API credentials."""
    _client().download_file(settings.r2_bucket_name, key, str(local_path))


def head_object(key: str) -> dict | None:
    """
    HEAD an object; None on missing or any error (probe, never raises).

    The size is what makes this more than an existence check: a presigned PUT
    cannot enforce Content-Length (S3 and R2 ignore it as a query parameter),
    so the only place a client's *declared* size can be checked against the
    bytes it actually wrote is here, after the fact — see confirm_upload.
    """
    try:
        resp = _client().head_object(Bucket=settings.r2_bucket_name, Key=key)
    except Exception:
        return None
    return {
        "size": resp.get("ContentLength", 0),
        "content_type": resp.get("ContentType", ""),
    }


def object_exists(key: str) -> bool:
    """HEAD an object; False on missing or any error (probe, never raises)."""
    return head_object(key) is not None


def head_bytes(key: str, count: int) -> bytes | None:
    """First `count` bytes of an object; None on missing or any error.

    A probe, never raises — same contract as head_object, and for the same
    reason: the caller is better placed to decide what a missing answer means
    than this function is. confirm_upload treats None as "allow", which is the
    opposite of what it does with a None from head_object; see the reasoning
    there.

    Ranged so that sniffing a container header costs a few bytes rather than a
    download — the objects on this path run to 8 GB.
    """
    try:
        resp = _client().get_object(
            Bucket=settings.r2_bucket_name, Key=key, Range=f"bytes=0-{count - 1}"
        )
        return resp["Body"].read(count)
    except Exception:
        return None


# ── Presigned direct-to-R2 uploads (CF-163) ──────────────────────────────────
# The browser sends video straight to R2 so the api never handles the bytes.
# generate_presigned_url is pure local signing — no network, safe to call on
# the event loop. Everything else here is a blocking API call and must be run
# through run_in_threadpool by its caller.


def presign_put(key: str, content_type: str, expires_in: int) -> str:
    """
    Presigned URL for a single-shot PUT of a whole object.

    ContentType is deliberately signed into the URL, so R2 rejects a PUT whose
    Content-Type *header* differs from the signed value.

    That binds the header, not the bytes (CF-244). A client can declare
    video/mp4, send the matching header so the signature verifies, and upload
    anything at all. This is a control over what the object is *labelled*, not
    over what it contains — confirm_upload sniffs the stored bytes for that.
    """
    return _client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.r2_bucket_name,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_in,
    )


def create_multipart(key: str, content_type: str) -> str:
    """Begin a multipart upload; returns the upload id."""
    resp = _client().create_multipart_upload(
        Bucket=settings.r2_bucket_name,
        Key=key,
        ContentType=content_type,
    )
    return resp["UploadId"]


def presign_upload_part(key: str, upload_id: str, part_number: int, expires_in: int) -> str:
    """Presigned URL for one part of an in-progress multipart upload."""
    return _client().generate_presigned_url(
        "upload_part",
        Params={
            "Bucket": settings.r2_bucket_name,
            "Key": key,
            "UploadId": upload_id,
            "PartNumber": part_number,
        },
        ExpiresIn=expires_in,
    )


def complete_multipart(key: str, upload_id: str, parts: list[dict]) -> None:
    """
    Assemble the uploaded parts into the final object.

    `parts` is [{"PartNumber": int, "ETag": str}, ...] and must be sorted by
    part number — S3 and R2 reject an out-of-order list.
    """
    _client().complete_multipart_upload(
        Bucket=settings.r2_bucket_name,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={"Parts": sorted(parts, key=lambda p: p["PartNumber"])},
    )


def abort_multipart(key: str, upload_id: str) -> None:
    """
    Discard an unfinished multipart upload and its parts.

    Uploaded parts are billed until aborted, so this runs on delete and on the
    abandoned-upload sweep. The bucket's abort-incomplete-multipart lifecycle
    rule is the backstop for uploads neither path reaches.
    """
    _client().abort_multipart_upload(
        Bucket=settings.r2_bucket_name,
        Key=key,
        UploadId=upload_id,
    )


# A dash flanked by whitespace, repeated — i.e. two or more separators that
# ended up adjacent once what sat between them was dropped. Anchored on the
# spaces so "00-04-12" and "Under-14" are not separators and cannot match.
_ORPHANED_SEPARATORS = re.compile(r"\s-(?:\s*-)+\s")


def content_disposition(filename: str) -> str:
    """`attachment` plus the filename, in both forms RFC 6266 defines.

    Two parameters, deliberately. `filename=` is a quoted string and can only
    carry ASCII, so a Cyrillic or CJK name has to be spelled a second time as
    RFC 5987's `filename*=UTF-8''…` — every current browser prefers `filename*`
    when both are present, and the ASCII one is what an old client falls back
    to. Emitting only the escaped form would name the file for nobody; emitting
    only the ASCII form is what dropped non-Latin titles on the floor.

    The filtering here is a header concern, not a filename one — services/
    filenames.py has already removed what is illegal *on disk*. What is left to
    close is the quoted string itself: a surviving `"` ends it and lets the rest
    of the value be read as further parameters, and a CR or LF would split the
    header outright. `filename*` is percent-encoded, so it cannot carry either,
    but it is filtered on the same pass rather than trusted to the encoder.

    The ASCII form falls back to "download" when no ASCII survives, keeping the
    extension: `filename=""` names the file after the URL's last path segment,
    which is the UUID this whole scheme exists to replace.

    **The fallback degrades and is meant to.** Where a name's only distinguishing
    text is non-ASCII, what is left is the scheme's fixed parts: two Cyrillic-
    titled games both fall back to `(condensed).mp4`, and nothing here can tell
    them apart short of transliterating, which would invent a spelling nobody
    chose. The clip scheme survives this because the timestamp is ASCII. It is
    accepted rather than fixed because `filename*` has been honoured by every
    shipping browser for over a decade — this parameter is for clients that are
    already reading a header from 2011.
    """
    kept = "".join(
        c for c in filename if c.isprintable() and c not in '";\\'
    ).strip(" .-")
    # The extension is held out of that fold rather than trimmed with the rest.
    # "Матч.mp4" reduces to ".mp4", and stripping the leading dot off it — which
    # has to happen, or the fallback names a hidden file — would leave a bare
    # "mp4" that no player will open.
    stem, _, ext = kept.rpartition(".")
    if not stem:
        stem, ext = kept, ""
    # Dropping the non-ASCII characters leaves behind the gaps and separators
    # they sat between ("Матч - spike" -> " - spike"), so the fallback is
    # re-collapsed and re-stripped rather than handed over as-is.
    ascii_stem = "".join(c for c in stem if c.isascii())
    ascii_stem = " ".join(ascii_stem.split())
    # Collapsing the whitespace is not enough on its own: a component between
    # two others leaves the separators on *both* sides of it, and they survive
    # as an orphaned run — a Cyrillic title with a Cyrillic player gave
    # "spike - - 00-04-12". Only a dash flanked by spaces is a separator here,
    # so this cannot touch the timestamp or a hyphenated word.
    ascii_stem = _ORPHANED_SEPARATORS.sub(" - ", ascii_stem).strip(" .-") or "download"
    ascii_ext = "".join(c for c in ext if c.isascii())
    ascii_name = f"{ascii_stem}.{ascii_ext}" if ascii_ext else ascii_stem
    header = f'attachment; filename="{ascii_name}"'
    if kept and not kept.isascii():
        # safe="" so nothing outside RFC 5987's attr-char set survives
        # unencoded; over-encoding is explicitly allowed, under-encoding is not.
        header += "; filename*=UTF-8''" + quote(kept, safe="")
    return header


def presign_url(key: str, expires_in: int = 3600, download_filename: str | None = None) -> str:
    """
    Generate a presigned URL for reading a file from R2 (default 1 hour).

    download_filename, when set, makes R2 serve Content-Disposition:
    attachment so a plain link click downloads the file under that name —
    the <a download> attribute is ignored for cross-origin URLs. Media
    elements ignore the header, so the same URL still plays inline.
    """
    params: dict[str, str] = {"Bucket": settings.r2_bucket_name, "Key": key}
    if download_filename:
        params["ResponseContentDisposition"] = content_disposition(download_filename)
    return _client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires_in,
    )


def presign_from_stored_url(
    stored_url: str, expires_in: int = 3600, download_filename: str | None = None
) -> str:
    """Convert a stored public URL back to a presigned URL."""
    prefix = f"{settings.r2_public_url}/"
    if stored_url.startswith(prefix):
        key = stored_url[len(prefix):]
    else:
        # Fallback: extract path from URL
        from urllib.parse import urlparse
        key = urlparse(stored_url).path.lstrip("/")
    return presign_url(key, expires_in, download_filename=download_filename)


def delete_file(key: str) -> None:
    _client().delete_object(Bucket=settings.r2_bucket_name, Key=key)


def list_objects(prefix: str) -> Iterator[dict]:
    """
    Yield {"key", "last_modified"} for every object under `prefix`.

    Paginated, so a prefix with more than 1000 objects is walked in full rather
    than silently truncated at the first page. Blocking API calls — a caller on
    the event loop must go through run_in_threadpool.
    """
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.r2_bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield {"key": obj["Key"], "last_modified": obj["LastModified"]}


def delete_files(keys: Sequence[str]) -> list[str]:
    """
    Delete many objects in batches of 1000 (the S3/R2 per-request maximum).

    Returns the keys that were NOT deleted, so a caller can log or retry them;
    a batch that fails outright is reported the same way rather than raising,
    since one bad batch must not abandon the rest. Deleting a missing key is
    not an error — S3 and R2 both report it as deleted.
    """
    failed: list[str] = []
    client = _client()
    for i in range(0, len(keys), 1000):
        batch = list(keys[i:i + 1000])
        try:
            resp = client.delete_objects(
                Bucket=settings.r2_bucket_name,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
        except Exception:
            failed.extend(batch)
            continue
        failed.extend(err["Key"] for err in resp.get("Errors", []))
    return failed


def plan_part_count(size_bytes: int, part_size: int) -> int:
    """
    How many multipart parts a file of `size_bytes` needs.

    The web client slices the file with the same ceil division, so both sides
    agree on part boundaries without exchanging them. A zero-byte file still
    needs one part — S3 rejects a multipart upload with no parts at all.
    """
    if part_size <= 0:
        raise ValueError("part_size must be positive")
    return max(1, -(-size_bytes // part_size))


def game_raw_key(game_id: uuid.UUID, filename: str) -> str:
    """
    Storage key for a game's source video, keeping only the extension.

    The extension is reduced to plain alphanumerics: it is the one part of a
    user-supplied filename that survives into the key, and the key is embedded
    in `raw_video_url`, which every consumer parses back out with urlparse. A
    "?" or "#" in the extension would silently truncate the key on that round
    trip, leaving the row pointing at an object that doesn't exist.
    """
    raw_ext = Path(filename).suffix.lstrip(".")
    ext = "".join(c for c in raw_ext if c.isalnum())[:10].lower()
    return f"raw/{game_id}.{ext}" if ext else f"raw/{game_id}"


def clip_key(game_id: uuid.UUID, clip_id: uuid.UUID) -> str:
    return f"clips/{game_id}/{clip_id}.mp4"


def thumbnail_key(game_id: uuid.UUID, clip_id: uuid.UUID) -> str:
    return f"thumbs/{game_id}/{clip_id}.jpg"


def condensed_key(game_id: uuid.UUID) -> str:
    # Deterministic per game so task retries overwrite instead of orphaning.
    return f"condensed/{game_id}.mp4"


def avatar_key(user_id: uuid.UUID) -> str:
    # Deterministic per user (CF-107): re-uploading an avatar overwrites the
    # previous object instead of accumulating one per change. Keyed by user id
    # rather than username so a handle rename doesn't strand the image.
    #
    # No extension: ALLOWED_AVATAR_TYPES accepts JPEG, PNG and WebP, so a fixed
    # `.jpg` would mislabel two of the three. The object's ContentType carries
    # the real type, and keeping the key extensionless means switching format
    # overwrites the old avatar rather than orphaning it under another suffix.
    #
    # Store the plain `{r2_public_url}/{key}` form. Do NOT append a cache-buster
    # to the recorded value: presign_from_stored_url slices the prefix off to
    # recover the Key, so a query string ends up inside it and R2 404s on every
    # signature. Freshness comes from presigning on read instead — each response
    # carries a new signature, so a re-uploaded avatar is fetched again without
    # the stored value ever changing (see routers/profiles.py._serialize).
    return f"avatars/{user_id}"
