from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Zero-config local defaults, and the sentinels the production check below looks
# for. A blank-string check cannot see these settings go missing: the variable is
# absent, pydantic supplies the default, and the process runs on it. Nobody
# points production at localhost on purpose, so reaching one of these values in
# production means the variable was never set.
LOCAL_DATABASE_URL = "postgresql+asyncpg://postgres:password@localhost:5432/clipfarm"
LOCAL_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


# ── Production config guard (CF-172) ───────────────────────────────
# Render's env group only creates keys that carry a literal `value:`; every
# `sync: false` key is invisible until a human pastes it in. So on a fresh
# blueprint apply these are simply absent, and each one fails late and
# unrecognisably: DATABASE_URL falls back to the local default and the
# pre-deploy migration dies with `Connect call failed ('127.0.0.1', 5432)`,
# which reads as a Supabase outage; a blank R2 or Supabase credential
# survives boot and fails at the first upload or auth check instead.
#
# FIELD names, not env names — `Settings.env_name_for()` derives the variable to
# print, so a field that ever grows an alias keeps reporting the right one.
# test_config.py pins every entry against `model_fields`, which is what turns a
# typo here into a CI failure rather than an AttributeError on a production box.
#
# Not `sentry_dsn` — blank there means "monitoring off", a supported
# production state.
REQUIRED_IN_PRODUCTION = (
    "database_url",
    # Not a credential, and it fails in a place no server log shows: with the
    # localhost default the api boots clean and passes its health check, then
    # every request from the real web origin is CORS-blocked. A total frontend
    # outage whose only symptom is in the browser console.
    "cors_origins",
    "supabase_url",
    "supabase_service_role_key",
    "r2_account_id",
    "r2_access_key_id",
    "r2_secret_access_key",
    "r2_public_url",
)

# Required of the WORKER only, and therefore not checked by the validator below:
# `Settings` is shared config that the api imports too, and the api never calls
# Modal — refusing to boot the whole web service over a worker credential would
# be a worse failure than the one being fixed. Checked at worker boot instead
# (app/workers/celery_app.py), which is the process that needs them.
#
# They belong in the same guard rather than in a comment: since CF-164 no model
# ships in the worker image, so blank tokens are not "slow local CPU" any more.
# Every game comes out with trajectory-only labels — plausible, persisted, and
# wrong — which is the CF-172 failure mode one layer up.
REQUIRED_IN_PRODUCTION_WORKER = (
    "modal_token_id",
    "modal_token_secret",
    # Same bar as the Modal tokens, and it is the primary detection signal:
    # unset, run_detection() falls through to the pose-first path (tasks.py),
    # which persists clips with weaker boundaries and reports the run a success.
    "roboflow_api_key",
)


# Fields whose local default is indistinguishable from "never set". Checked in
# addition to emptiness, not instead of it — a key added to the Render group and
# left blank arrives as "" and never reaches the default at all, so both paths
# have to be covered.
LOCAL_DEFAULT_SENTINELS = {
    "database_url": LOCAL_DATABASE_URL,
    "cors_origins": LOCAL_CORS_ORIGINS,
}


def production_config_error(missing: list[str]) -> str:
    """The message both checks raise. Names the variables and where they come
    from on each deploy path — the whole point of CF-172 is that the old symptom
    named neither, and a message that names only one path sends half its readers
    to the wrong file.
    """
    return (
        "ENVIRONMENT=production but these required settings are not set: "
        + ", ".join(missing)
        + ". On Render these are declared `sync: false`, which means Render "
        "does not create them at all — they stay absent until someone adds "
        "them in the dashboard (DEPLOY_RENDER.md § Fill the secrets). On the "
        "VPS stack they come from .env.docker (DEPLOY.md § 4). Note that "
        "DATABASE_URL and CORS_ORIGINS have working localhost defaults, so "
        "\"unset\" here can mean the default is still in place."
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Environment
    #
    # "production" turns on the required-settings check below (CF-172). It is
    # NOT `debug` inverted: `debug` defaults to False because dev auth fallbacks
    # must be off unless asked for, so gating on `not debug` would make every
    # zero-config run — a bare `uvicorn`, CI's `pytest api/tests` — start
    # demanding secrets. This flag defaults the other way round, so the safe
    # default for each is its own default.
    #
    # Every real deployment sets it: render.yaml pins it in the clipfarm-shared
    # group with a literal `value:` (not `sync: false`), so it arrives without a
    # human step — the guard cannot be the thing you forgot to fill in.
    environment: str = "development"
    debug: bool = False  # Enables dev fallbacks (DEV_USER_ID auth, etc.)
    # Whether the pose-first fallback scan may return `_stub_detections` —
    # fabricated clips at invented timestamps, which the worker PERSISTS. This
    # is deliberately NOT `debug` (CF-164 review 3). `debug` is a general "dev
    # fallbacks" flag that lives in the shared api+worker env group, so setting
    # it to debug the api would silently grant the worker permission to write
    # invented highlights to the database. A switch that decides whether made-up
    # data reaches production deserves its own name and its own default.
    allow_stub_detections: bool = False
    # Same localhost-default shape as cors_origins below, and deliberately NOT
    # in the production guard: nothing reads it. It configures no client and
    # builds no outbound URL today, so a stale value in production breaks
    # nothing. Move it into REQUIRED_IN_PRODUCTION the day something does.
    api_base_url: str = "http://localhost:8000"
    cors_origins: str = LOCAL_CORS_ORIGINS  # comma-separated

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

    # ffmpeg encode/decode threads per cut (CF-224). ffmpeg reads the visible
    # core count, which in a container is the host's — on Render's 16-core hosts
    # x264 spawned ~24 encoder threads and its per-thread 1080p frame buffers
    # OOM-killed the worker on its then-512 MB plan. The right value follows the
    # instance plan, not the host. 2 is the safe default for any container;
    # production sets 1 in render.yaml. That 1 was measured at 0.5 CPU, where a
    # second encoder thread was pure memory cost; the worker is on a whole CPU
    # since CF-240 and 1 was carried over deliberately rather than re-tuned in
    # the same change (CF-223 measures it). Raise it in the env when the box
    # actually has cores to use.
    #
    # ge=1 because 0 is not "no threads" to ffmpeg — it is the *auto* sentinel,
    # which restores exactly the host-sized pool this setting exists to prevent,
    # and would do it silently. A negative fails every cut inside the pipeline's
    # swallowing except, yielding a game with no clips at all. Both are better
    # caught at boot.
    ffmpeg_threads: int = Field(default=2, ge=1)

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
    # Note under condense_mode="guarded": motion anchors open windows on their
    # own evidence, so this bounds only the contact-derived half. Raising it to
    # be conservative buys less than it reads as buying — the anchor is
    # unaffected. Under "rules" it means what it always did.
    condense_min_contacts: int = 1           # keep even single-contact groups — dropping play is worse
    condense_merge_gap_seconds: float = 5.0  # merge windows closer than this
    # Motion bridge: re-join windows split by contact-silent stretches of real
    # play (far-court possessions, occlusions). Bridges a gap when enough of
    # the tracked ball's speed samples inside it are fast — in-play flight is
    # fast, between-rally ball handling is mostly slow.
    condense_bridge_speed_pxps: float = 150.0   # a speed sample this fast counts as in-play
    condense_bridge_fast_fraction: float = 0.35  # bridge when ≥ this fraction of samples are fast
    condense_bridge_max_seconds: float = 20.0   # never bridge gaps longer than this
    # Which keep-window builder the condense stage uses. A failure *inside* the
    # guarded builder falls back to "rules", so a feature mismatch degrades the
    # condense rather than failing the run. Note the fallback is within-builder
    # only: both builders are imported from the same module, so an import-time
    # failure takes out "rules" too and the whole condense stage is skipped by
    # the stage-level handler in tasks.py. The game still ships `ready`.
    #   "rules"    the two blocks above: contacts + motion bridge (CF-46)
    #   "guarded"  speed-gated contacts + motion anchors + tight pads, abstaining
    #              when the ball track is too sparse to judge (CF-187)
    # Default is "guarded". Measured at 42b582f from the R2 ball caches, with
    # mode=rules reproducing its documented figures exactly on four of five
    # fixtures as a control (the fifth, test5's 9.6% rules baseline, was a
    # long-standing doc error and is corrected).
    #
    # Dead time: it removes more on all four it condenses (+2.2 points on test1,
    # +41 test2, +29 test4, +40 test5). Live play: it cuts 50s LESS than "rules"
    # on test1 and more on the other three (2s->12s, 83s->100s, 0s->20s), and on
    # the fifth it abstains rather than cut 118s of rally.
    #
    # Net at the harness' 4:1 live-cut exchange rate: +533s vs +134s over the
    # three fixtures comparable across games. Counting all five gives +1440s vs
    # +633s, and that larger figure is the misleading one: test1 and test3 supply
    # most of the gap and their own fixture notes exclude them from cross-game
    # comparison. Three of the five were held out while it was tuned.
    #
    # Worth reading past the net: across those three comparable fixtures "rules"
    # cuts 85s of live play and "guarded" cuts 132s, so the default cuts 47s more
    # of the thing this repo protects and buys it back with dead time at 4:1.
    # Raising the gate or anchor speeds widens that — see ml/eval/README.md.
    # Literal, not str: an unknown value here (CONDENSE_MODE=Guarded) would
    # otherwise load fine and silently run "rules" forever behind one warning
    # per game. Note what "fail at load" means for an operator: Settings is
    # constructed at import, so a typo here is a hard boot failure of both api
    # and worker with a pydantic validation error — not a curated message like
    # production_config_error's. That is the intended trade (a misconfigured
    # builder is worse than a refused boot), and it is safe to make because no
    # deploy file sets CONDENSE_MODE: render.yaml, the compose files and
    # .env.docker.example all leave it at this default.
    #
    # What rolling back does NOT restore: the 2%-trim floor
    # (CONDENSE_MIN_TRIM_FRACTION in workers/tasks.py) is new in CF-187 and
    # applies to both builders, so a game whose rule-based windows keep >=98%
    # of the video now ships without a condensed cut where it would previously
    # have re-encoded a near-copy. CONDENSE_MODE=rules restores the *builder*,
    # not every line the card touched.
    condense_mode: Literal["rules", "guarded"] = "guarded"
    # Guarded-path tunables — see ml/pipeline/dead_time.active_windows_guarded.
    # Speeds are frame-heights/s, so they hold across source resolutions.
    condense_guard_gate_speed: float = 0.25       # min median speed around a credible contact
    condense_guard_anchor_speed: float = 0.30     # a speed sample this fast counts toward an anchor
    # Pads apply to contact-derived windows only — anchor windows already span
    # the motion they found and keep their own pad (dead_time.ANCHOR_PAD), so
    # raising these does not widen a rally recovered purely by the anchor.
    condense_guard_pad_before: float = 3.0        # seconds kept before a window
    condense_guard_pad_after: float = 2.0         # seconds kept after a window
    condense_guard_merge_gap_seconds: float = 3.0  # merge windows closer than this
    # Usable speed samples/s below which the builder abstains and keeps the whole
    # video. The fixtures observe 0.57/s on the one game that must abstain and
    # 1.51-2.99/s on the four that must not, so 1.0 sits in a wide clean gap — but
    # that also means the boundary is UNCONSTRAINED, not tuned: nothing has been
    # measured between 0.57 and 1.51, and a game landing in there flips between
    # condensing normally and shipping no condensed video at all, the most
    # user-visible outcome on this path. Revisit with a real game in the gap
    # before moving it.
    # Units: *usable speed samples*/s (speed_samples() output), not raw track
    # points. test3 is 0.57 usable/s but 0.76 raw/s, and its fixture note records
    # the raw one — quoting the wrong figure against this threshold compares two
    # different measurements.
    condense_guard_min_track_rate: float = 1.0

    # Database
    database_url: str = LOCAL_DATABASE_URL
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
    # 3600s: a task still running past it is redelivered while it runs. Before
    # CF-184 that was the CF-45 duplicate-processing root cause, and the fix was
    # "size above worst-case task time" — back then the local-CPU fallback ran at
    # ~1.3–2x realtime, so a long match with Modal down could run for hours.
    #
    # CF-192 asked whether the CF-167 cap raise (2 GB → 8 GB, longer videos) means
    # this must rise to match. It does not, and the most direct reason is that the
    # premise no longer holds where it is deployed: since CF-164 every model runs
    # on Modal and the worker image ships no torch/ultralytics/inference
    # (Dockerfile.api) — "local-CPU ball tracking is gone", restorable only by
    # installing ml/requirements.txt outside the image for dev. So in production
    # the worst-case task time is the Modal path (single-digit-minute tracking),
    # nowhere near 2h; a job that outruns this timeout is a dev-stack event, not a
    # production one. The card's 2.6–4h CPU figure is absent in the image, not
    # merely rare.
    #
    # Two further facts, for when that dev-stack redelivery does happen:
    #   • Correctness does not depend on the timeout. CF-184's session-scoped
    #     advisory lock (app/workers/locks.py) — not this number — is what makes
    #     concurrent double-processing impossible: a redelivery finds the lock
    #     held by the still-running original and no-ops.
    #   • That no-op is not free, and neither is a re-run — so redelivery is a
    #     defect, not a harmless event (an earlier draft called it harmless). The
    #     duplicate returns; acks_late acks it, so the original's delivery is now
    #     spent, and a hard kill after that strands the game in `processing` with
    #     nothing to requeue it (the #149 reaper was retired as superseded by the
    #     lock, which preserves mutual exclusion, not at-least-once). And when the
    #     duplicate instead finds the lock free (original finished), it RE-RUNS:
    #     CF-37's refresh hard-deletes the clips and re-creates them with fresh
    #     uuids, and collection_clips.clip_id / corrections.clip_id are ON DELETE
    #     CASCADE — so a re-run silently drops curated collections and user
    #     corrections. Cheap in compute (ball positions cached, CF-11), not data.
    #
    # Why not raise it anyway, to be safe? Raising above worst-case WOULD close
    # the spent-delivery hole — no redelivery means no no-op to consume the
    # original's delivery, which is exactly what "size above worst-case" bought
    # under CF-45. But it buys that at proportionally longer recovery latency (the
    # too-high cost below), leaves the destructive re-run untouched (that fires on
    # any legitimate kill-and-requeue too), and is unnecessary given the image has
    # no multi-hour path. The two defects' real fixes live elsewhere:
    # self.retry(countdown=...) in the duplicate branch for the delivery hole, and
    # decoupling clip ids from re-runs for the cascade. So 2h stays:
    #   • too high → on the Redis transport a hard-killed worker's task is only
    #     restored to the queue after this timeout elapses (there is no push
    #     cancel). It is a floor, not a ceiling: under --pool=solo
    #     `restore_visible` runs only while the worker polls the broker, so a
    #     worker busy on another game defers the restore — the real wait is 2h
    #     plus whatever else it processes first. This is the live reason to keep
    #     it low.
    #   • too low → the dev-stack redelivery above; harmless to correctness,
    #     costly to a dev's data, and not a production concern.
    celery_visibility_timeout: int = 7200    # 2h
    # CF-184: the per-game lock is a session-scoped Postgres advisory lock
    # (app/workers/locks.py), so it has no TTL to tune against the timeout above
    # — it is released when the holding connection dies. What replaced
    # `process_lock_ttl_seconds` is nothing at all, deliberately: a TTL was only
    # ever an approximation of "is the holder alive", and it was the reason a
    # hard-killed worker stranded a game in `processing`.

    # Ball detection (Roboflow). Declared here for the worker's production
    # guard; the pipeline call sites predate this class and read
    # os.environ["ROBOFLOW_API_KEY"] directly, so the guard is only as good as
    # the real environment — which is what both production paths set. A value
    # reaching this field from an `.env` file alone would satisfy the check and
    # not the call sites.
    roboflow_api_key: str = ""

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

    @classmethod
    def env_name_for(cls, field: str) -> str:
        """The environment variable that sets `field`.

        pydantic-settings derives it by upper-casing the field name, unless the
        field declares an alias. Asking the model rather than assuming
        `field.upper()` keeps the error message honest if one ever does.
        """
        info = cls.model_fields[field]
        alias = info.validation_alias or info.alias
        return alias if isinstance(alias, str) else field.upper()

    def missing_in_production(self, fields: tuple[str, ...]) -> list[str]:
        """Env names of `fields` that production has not set. Empty elsewhere,
        so callers do not repeat the environment test."""
        if self.environment.strip().lower() != "production":
            return []

        return [
            self.env_name_for(field)
            for field in fields
            if not str(getattr(self, field)).strip()
            or getattr(self, field) == LOCAL_DEFAULT_SENTINELS.get(field)
        ]

    @model_validator(mode="after")
    def _check_production_settings(self) -> "Settings":
        missing = self.missing_in_production(REQUIRED_IN_PRODUCTION)
        if missing:
            raise ValueError(production_config_error(missing))
        return self


settings = Settings()
