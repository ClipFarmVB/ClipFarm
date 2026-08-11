from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Environment
    debug: bool = False  # Enables dev fallbacks (DEV_USER_ID auth, etc.)
    api_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"  # comma-separated

    # Upload limits
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GB
    allowed_upload_content_types: str = "video/mp4,video/quicktime,video/x-matroska,video/webm"

    # Social surface (CF-107+). Off by default: public identity ships behind a
    # flag so the epic can land incrementally without exposing profiles,
    # handles or avatars in production before the whole of it is ready. The web
    # half reads NEXT_PUBLIC_SOCIAL_ENABLED — both must be on for the feature to
    # work, and either being off is a coherent state.
    social_enabled: bool = False

    # ML pipeline
    clip_verify_enabled: bool = False  # Use CLIP frames in highlight scoring (slow on CPU, enable for GPU)
    # Tuned on real footage: 0.50 turns a 22-min VOD into ~5 min of clips
    # while keeping verified highlights. Override via env without rebuild.
    highlight_score_threshold: float = 0.50  # Rallies scoring below this are dropped before pose/cutting

    # Pose refinement (classify_within_windows). Production defaults; the
    # Docker dev stack overrides these to lighter values for CPU speed.
    pose_model: str = "yolov8s-pose.pt"
    pose_imgsz: int = 1280
    pose_skip_frames: int = 4

    # Dead-time removal (opt-in condense stage). Windows come from the same
    # ball/pose signals as highlight clips — these only shape the keep-windows.
    # Loosened in CF-46: the CF-24 values (gap 5.0, pad 2.0/2.5, min_contacts 2)
    # trimmed real rallies. Tuned loose on purpose — keeping some dead time
    # beats cutting play. pad_before covers the serve ritual when tracking
    # missed the serve contact and the first detection is the receive.
    condense_gap_seconds: float = 10.0       # contact gap that counts as dead time
    condense_pad_before: float = 5.0         # seconds kept before a window
    condense_pad_after: float = 4.0          # seconds kept after a window
    condense_min_contacts: int = 1           # keep even single-contact groups — dropping play is worse
    condense_merge_gap_seconds: float = 5.0  # merge windows closer than this
    # Motion bridge: re-join windows split by contact-silent stretches of real
    # play (far-court possessions, occlusions). Bridges a gap when enough of
    # the tracked ball's speed samples inside it are fast — in-play flight is
    # fast, between-rally ball handling is mostly slow.
    condense_bridge_speed_pxps: float = 150.0   # a speed sample this fast counts as in-play
    condense_bridge_fast_fraction: float = 0.35  # bridge when ≥ this fraction of samples are fast
    condense_bridge_max_seconds: float = 20.0   # never bridge gaps longer than this

    # Database
    database_url: str = "postgresql+asyncpg://postgres:password@localhost:5432/clipfarm"

    # Auth / JWT (legacy — Supabase JWKS is the source of truth)
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Supabase (optional — used for Auth verification)
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_content_types_set(self) -> set[str]:
        return {c.strip() for c in self.allowed_upload_content_types.split(",") if c.strip()}

    # Cloudflare R2 / S3
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "clipfarm"
    r2_public_url: str = ""

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    # Worker redelivery safety (CF-65a). Redis' default visibility timeout is
    # 3600s, so a task longer than that is silently redelivered — the CF-45
    # duplicate-processing root cause. Set it above worst-case task time. Note
    # the local-CPU fallback is slow (~1.3–2x realtime tracking), so a long match
    # with Modal unavailable can run for hours; size for your slowest path.
    celery_visibility_timeout: int = 7200    # 2h
    # The per-game lock MUST outlive the visibility timeout, not equal it: a
    # redelivery fires at ~visibility_timeout, and if the lock lapses at the same
    # instant a second worker acquires it and runs concurrently — the exact bug
    # this prevents. Keeping the lock longer means the redelivered copy always
    # finds the lock still held and no-ops. (Trade-off: a hard-killed worker
    # orphans the lock until this TTL; a stale-"processing" reaper is the fix —
    # see #149, which gates the Render cutover in #98.)
    process_lock_ttl_seconds: int = 10800    # 3h — deliberately > visibility_timeout

    # Modal
    modal_token_id: str = ""
    modal_token_secret: str = ""

    # Error monitoring (Sentry — CF-89). Empty DSN = disabled (local/dev default).
    # The same DSN is shared by the api and worker processes; the web app uses
    # NEXT_PUBLIC_SENTRY_DSN separately.
    sentry_dsn: str = ""
    sentry_environment: str = "development"
    sentry_release: str = ""
    sentry_traces_sample_rate: float = 0.0  # 0 = errors only, no perf tracing (avoids overhead/cost)

    # Delete raw uploads after N days (0 = keep forever)
    raw_upload_retention_days: int = 7


settings = Settings()
