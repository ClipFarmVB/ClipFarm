"""Cloudflare R2 (or AWS S3) object storage helpers."""
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

from app.config import settings


class LimitedReader:
    """
    Wraps a file-like object and raises ValueError if more than `limit` bytes
    are read. Passed directly to upload_fileobj to enforce size limits even
    when the client omits a Content-Length header.
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


def object_exists(key: str) -> bool:
    """HEAD an object; False on missing or any error (probe, never raises)."""
    try:
        _client().head_object(Bucket=settings.r2_bucket_name, Key=key)
        return True
    except Exception:
        return False


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
        # R2 reflects this into a response header — keep it printable ASCII
        # and quote-free so it can't escape the quoted-string.
        safe = "".join(
            c for c in download_filename
            if c.isascii() and c.isprintable() and c not in '";\\'
        ).strip() or "download"
        params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
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


def game_raw_key(game_id: uuid.UUID, filename: str) -> str:
    ext = Path(filename).suffix
    return f"raw/{game_id}{ext}"


def clip_key(game_id: uuid.UUID, clip_id: uuid.UUID) -> str:
    return f"clips/{game_id}/{clip_id}.mp4"


def thumbnail_key(game_id: uuid.UUID, clip_id: uuid.UUID) -> str:
    return f"thumbs/{game_id}/{clip_id}.jpg"


def condensed_key(game_id: uuid.UUID) -> str:
    # Deterministic per game so task retries overwrite instead of orphaning.
    return f"condensed/{game_id}.mp4"
