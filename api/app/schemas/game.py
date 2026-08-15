import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.game import GameStatus


def _clean_title(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("title cannot be empty")
    if len(v) > 255:
        raise ValueError("title too long (max 255 characters)")
    return v


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: GameStatus
    progress: float = 0.0
    progress_stage: str | None = None
    created_at: datetime
    clip_count: int | None = None
    condense_requested: bool = False
    condensed_video_url: str | None = None
    original_duration: float | None = None
    condensed_duration: float | None = None


class GameCreate(BaseModel):
    title: str


class GameRename(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        return _clean_title(v)


# ── Presigned direct-to-R2 upload flow (CF-163) ──────────────────────────────


class UploadConfig(BaseModel):
    """
    Limits the client must respect, served by the api so the browser never
    hardcodes them. The UI used to advertise its own 15 GB cap against a 2 GB
    server limit, which meant an in-between file was only rejected after the
    bytes had already moved.
    """

    max_upload_bytes: int
    allowed_content_types: list[str]
    single_put_max_bytes: int
    part_size_bytes: int
    url_ttl_seconds: int

    # Per-user processing quota (CF-91). Served alongside the static limits so
    # the client can show the remaining allowance before a file is chosen, and
    # so "why was this rejected" is answerable without a failed upload.
    max_duration_seconds: float
    window_hours: float
    max_games_per_window: int
    games_used: int
    games_remaining: int
    max_minutes_per_window: float
    minutes_used: float
    minutes_remaining: float

    @classmethod
    def from_status(cls, status, **static) -> "UploadConfig":
        """Build from a `quota.QuotaStatus` plus the transfer-shape settings."""
        limits = status.limits
        return cls(
            max_upload_bytes=limits.max_upload_bytes,
            allowed_content_types=list(limits.allowed_content_types),
            max_duration_seconds=limits.max_duration_seconds,
            window_hours=limits.window_hours,
            max_games_per_window=limits.max_games_per_window,
            games_used=status.games_used,
            games_remaining=status.games_remaining,
            max_minutes_per_window=limits.max_minutes_per_window,
            minutes_used=round(status.minutes_used, 1),
            minutes_remaining=round(status.minutes_remaining, 1),
            **static,
        )


class UploadCreate(BaseModel):
    """What the client declares up front, before any bytes move."""

    title: str
    filename: str = Field(max_length=255)
    content_type: str = Field(max_length=255)
    # Declared, not proven — the size is re-checked against the object itself
    # at completion, since a presigned PUT cannot enforce Content-Length.
    size_bytes: int = Field(gt=0)
    # Also declared, and also not proven: the api never decodes the video. It
    # gates the obvious over-length upload here and charges the quota at
    # completion; the worker's probe is what actually settles both (CF-91).
    # ge=0 because the value is charged against the minute quota — a negative
    # one would credit the sender.
    duration_seconds: float | None = Field(default=None, ge=0)
    condense: bool = False

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        return _clean_title(v)


class UploadPart(BaseModel):
    part_number: int
    url: str


class UploadTicket(BaseModel):
    """
    Everything the browser needs to upload without talking to the api again
    until it is done.

    `mode` picks the shape: "single" uses `upload_url`, "multipart" uses
    `upload_id` + `parts`, slicing the file at `part_size_bytes`.
    """

    game_id: uuid.UUID
    mode: Literal["single", "multipart"]
    content_type: str
    expires_in: int
    upload_url: str | None = None
    upload_id: str | None = None
    part_size_bytes: int | None = None
    parts: list[UploadPart] = []


class CompletedPart(BaseModel):
    part_number: int = Field(gt=0)
    etag: str


class UploadComplete(BaseModel):
    """Empty for a single PUT; one entry per part for a multipart upload."""

    parts: list[CompletedPart] = []
