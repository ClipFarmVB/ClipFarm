from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Environment
    debug: bool = False  # Enables dev fallbacks (DEV_USER_ID auth, etc.)
    # Whether the pose-first fallback scan may return `_stub_detections` —
    # fabricated clips at invented timestamps, which the worker PERSISTS. This
    # is deliberately NOT `debug` (CF-164 review 3). `debug` is a general "dev
    # fallbacks" flag that lives in the shared api+worker env group, so setting
    # it to debug the api would silently grant the worker permission to write
    # invented highlights to the database. A switch that decides whether made-up
    # data reaches production deserves its own name and its own default.
    allow_stub_detections: bool = False
    api_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"  # comma-separated

    # Upload limits
    # 8 GB (CF-167). The old 2 GB was sized for an api that proxied the bytes;
    # since CF-163 the browser PUTs straight to R2, so the ceiling is a cost
    # decision rather than a transfer one.
    #
    # What 8 GB actually buys, since the honest answer is "it depends on the
    # camera": ~2 h of HEVC 1080p30 at ~8 Mbps (7.2 GB — the tight case, not a
    # comfortable one), ~1 h of H.264 1080p60, ~30 min of 4K. A full 4K match
    # does NOT fit and is rejected up front.
    #
    # Note this binds well before max_upload_duration_seconds below for
    # anything over ~4.4 Mbps: the 4 h duration cap is the ceiling on cheap
    # footage, this is the ceiling on expensive footage, and both apply.
    max_upload_bytes: int = 8 * 1024 * 1024 * 1024  # 8 GB
    allowed_upload_content_types: str = "video/mp4,video/quicktime,video/x-matroska,video/webm"
    # Hard duration cap (CF-91). GPU inference costs roughly $0.25 per hour of
    # footage, so an unbounded upload is an unbounded bill. 4 h clears a long
    # 5-set match with room to spare — the limit is aimed at multi-hour
    # stream dumps, not at any real game.
    max_upload_duration_seconds: float = 4 * 3600  # 4 h

    # Per-user processing quota (CF-91), evaluated over a rolling window.
    # Both caps apply: whichever is hit first stops the upload. Defaults allow
    # a full tournament day (5 matches / 6 h ≈ $1.50 of GPU) while bounding
    # what a single account can spend per day. Plan tiers (CF-64) will raise
    # these per user rather than replace them — see services/quota.py.
    quota_window_hours: float = 24.0
    quota_max_games_per_window: int = 5
    quota_max_minutes_per_window: float = 360.0  # 6 h of footage

    # Direct-to-R2 uploads (CF-163). The browser PUTs to R2 with a presigned
    # URL; the api only issues the ticket and confirms the object afterwards.
    #
    # TTL has to cover a whole upload, and every part URL in a multipart ticket
    # shares this one expiry with no renewal path — an expired URL 403s, which
    # upload.ts treats as non-retryable (correctly: a signature failure repeats
    # forever), so a ticket that lapses mid-transfer loses the whole thing.
    # 12h means a cap-sized 8 GB upload needs only ~1.6 Mbps sustained to
    # finish, under any uplink that could have started it; at 6h it needed 3.2.
    # The URL is still a write grant for one specific key, so the exposure this
    # trades away stays bounded and same-day.
    upload_url_ttl_seconds: int = 12 * 3600
    # Files at or below this go as one PUT: a single round trip, no completion
    # bookkeeping. Above it, multipart — not because of R2's 5 GiB single-PUT
    # ceiling (which a 100 MiB threshold keeps out of reach) but for
    # resumability: a phone on cellular that drops at 90% retries one part, not
    # the whole file.
    single_put_max_bytes: int = 100 * 1024 * 1024
    # S3/R2 require every part except the last to be >= 5 MiB. 100 MiB keeps a
    # cap-sized 8 GB upload to ~80 parts (the 10,000-part limit is never in
    # reach) while still being a cheap unit to retry.
    upload_part_size_bytes: int = 100 * 1024 * 1024
    # A presigned ticket the client never completes leaves an `uploading` row
    # and possibly orphaned multipart parts. Rows older than this are swept on
    # the owner's next presign — no cron, no new infrastructure.
    abandoned_upload_hours: int = 24

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
    # Connection used only for the worker's per-game advisory lock (CF-184).
    # Empty = use database_url, which is right locally and for any direct or
    # session-mode connection. Set this when DATABASE_URL is a TRANSACTION-mode
    # pooler (Supabase port 6543): that mode can serve consecutive statements
    # from different backends, which cannot hold a session-scoped lock. Point it
    # at the session-mode port (5432) instead. The worker refuses to process
    # rather than run under a lock that does not hold — see locks.py.
    lock_database_url: str = ""
    # Ports refused outright as a lock connection: they serve a transaction-mode
    # pooler, which cannot hold a session-scoped lock, and no runtime check
    # detects that reliably (see locks.py). 6543 is Supabase's; a deployment
    # behind a PgBouncer on some other port declares it here, comma-separated.
    lock_pooler_ports: str = "6543"

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
    # CF-184: the per-game lock is a session-scoped Postgres advisory lock
    # (app/workers/locks.py), so it has no TTL to tune against the timeout above
    # — it is released when the holding connection dies. What replaced
    # `process_lock_ttl_seconds` is nothing at all, deliberately: a TTL was only
    # ever an approximation of "is the holder alive", and it was the reason a
    # hard-killed worker stranded a game in `processing`.

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
