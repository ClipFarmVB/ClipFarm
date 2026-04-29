"""Cloudflare R2 (or AWS S3) object storage helpers."""
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

from app.config import settings


def r2_configured() -> bool:
    """True only when real R2 credentials are present (not placeholder values)."""
    return bool(
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and not settings.r2_account_id.startswith("your-")
        and not settings.r2_access_key_id.startswith("your-")
    )


def _client():
    endpoint = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
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


def presign_url(key: str, expires_in: int = 3600) -> str:
    """Generate a presigned URL for reading a file from R2 (default 1 hour)."""
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket_name, "Key": key},
        ExpiresIn=expires_in,
    )


def presign_from_stored_url(stored_url: str, expires_in: int = 3600) -> str:
    """Convert a stored public URL back to a presigned URL."""
    prefix = f"{settings.r2_public_url}/"
    if stored_url.startswith(prefix):
        key = stored_url[len(prefix):]
    else:
        # Fallback: extract path from URL
        from urllib.parse import urlparse
        key = urlparse(stored_url).path.lstrip("/")
    return presign_url(key, expires_in)


def delete_file(key: str) -> None:
    _client().delete_object(Bucket=settings.r2_bucket_name, Key=key)


def game_raw_key(game_id: uuid.UUID, filename: str) -> str:
    ext = Path(filename).suffix
    return f"raw/{game_id}{ext}"


def clip_key(game_id: uuid.UUID, clip_id: uuid.UUID) -> str:
    return f"clips/{game_id}/{clip_id}.mp4"


def thumbnail_key(game_id: uuid.UUID, clip_id: uuid.UUID) -> str:
    return f"thumbs/{game_id}/{clip_id}.jpg"


def dead_time_raw_key(run_id: uuid.UUID, filename: str) -> str:
    ext = Path(filename).suffix
    return f"deadtime/raw/{run_id}{ext}"


def dead_time_clip_key(run_id: uuid.UUID, clip_id: uuid.UUID) -> str:
    return f"deadtime/clips/{run_id}/{clip_id}.mp4"


def dead_time_thumbnail_key(run_id: uuid.UUID, clip_id: uuid.UUID) -> str:
    return f"deadtime/thumbs/{run_id}/{clip_id}.jpg"
