# ClipFarm

ClipFarm turns raw volleyball game footage into a filterable feed of highlight clips.
Upload a full-game VOD → the pipeline tracks the ball to find rallies → scores each
rally for "highlight-worthiness" (crowd reaction + rally shape) → cuts the survivors
into clips, tagged by action type and (eventually) player. The result is a per-player,
score-sortable clip library instead of a 30-minute video nobody rewatches.

This document is the entry point for both humans onboarding and AI agents contributing.
It should be enough to understand how the system fits together and where to make a change.

---

## Tech Stack

| Layer | Stack |
|---|---|
| **web/** | Next.js 16 (App Router, Turbopack) · React · Tailwind CSS v4 · TypeScript |
| **api/** | FastAPI (Python 3.11) · SQLAlchemy 2 (async) · Alembic · Pydantic v2 |
| **ml/** | Roboflow `inference` (RF-DETR ball model, local) · YOLOv8-pose (ultralytics) · OpenCV · FFmpeg · CLIP (optional) |
| **Jobs** | Celery + Redis (broker + result backend) |
| **Data** | Supabase Postgres (RLS on all tables) · Supabase Auth (JWKS JWT) |
| **Storage** | Cloudflare R2 (S3-compatible, via boto3) — raw uploads, clips, thumbnails, ball-position cache |
| **GPU** | Modal (serverless T4) for ball tracking; graceful fall back to local CPU |
| **Dev** | Docker Compose (the standard path) |

---

## Architecture & Flow

A game is uploaded **straight from the browser to R2** with a presigned URL, and a
Celery task processes it end to end. The heavy, occlusion-proof primary path is
**ball tracking**, not pose.

```
Browser (web/, Next.js)
    │  1. POST /games/uploads  ───►  FastAPI: validate type + size, presign
    │  2. PUT video ──────────────►  R2: raw/{game_id}.mp4   (api not involved)
    │  3. POST /games/{id}/uploads/complete
    ▼
FastAPI (api/)  ──►  HEAD the object, then enqueue process_game
    │
    ▼
Celery worker  ──  process_game_task()  (api/app/workers/tasks.py)
    │
    ├─ 0. Download video + extract audio energy envelope (reused by stages 2 & 4)
    │
    ├─ 1. BALL TRACKING → rally windows            (ml/pipeline/ball.py)
    │     ├─ track_ball()  ← Modal GPU (T4) if configured, else local CPU
    │     │                  R2-cached by video MD5 + model + sample rate
    │     ├─ find_contacts()      ← ballistic-residual contact detection
    │     └─ contacts_to_rallies()← group contacts into clip windows
    │
    ├─ 2. HIGHLIGHT SCORING → gate                 (ml/pipeline/score.py, audio.py)
    │     ├─ score_cheers()   ← post-rally crowd reaction (audio)
    │     ├─ score_highlights ← cheer + rally shape (+ optional CLIP)
    │     └─ drop rallies below highlight_score_threshold (0.50)
    │
    ├─ 3. POSE REFINEMENT (survivors only)         (ml/pipeline/detect.py)
    │     └─ classify_within_windows() ← YOLOv8-pose overrides action label if more confident
    │
    ├─ 4. Audio confidence weighting               (ml/pipeline/audio.py)
    │
    └─ 5. Cut clips → upload → persist             (ml/pipeline/clip.py, _sync_db.py)
          └─ FFmpeg per clip → R2: clips/, thumbs/ → Clip rows in Postgres
```

If `ROBOFLOW_API_KEY` is unset or ball tracking fails entirely, the pipeline falls back
to a pose-first full-video scan (`ml/pipeline/detect.py::run_detection`) — the rare path.

---

## Repository Layout

```
web/                       Next.js frontend
  src/app/                 routes: /, /login, /signup, /games, /games/[id], /collections, /upload
  src/lib/api.ts           API client (clips support min_score + sort=score)
  src/components/          UploadZone, ClipCard, ClipModal, Sidebar, ui/
  AGENTS.md                ⚠ read before touching web — this Next.js has breaking changes vs training data

api/app/
  main.py                  FastAPI app + router wiring + CORS
  config.py                ALL settings (env-driven). Start here to see every knob.
  auth.py                  Supabase JWKS JWT verification (FastAPI dependency + middleware)
  database.py              async SQLAlchemy engine/session
  models/                  User, Team, Player, Game, Clip, Collection, Correction, DeadTime{Run,Clip}
  routers/                 games, clips, players, collections, dead_time
  schemas/                 Pydantic request/response models
  services/storage.py      R2/S3 helpers (upload, download, presign, delete, key builders)
  workers/
    tasks.py               Celery pipeline — process_game_task is the spine of the app
    _sync_db.py            sync DB helpers for Celery (no asyncio loop inside tasks)
    celery_app.py          Celery config
  alembic/                 migrations

ml/pipeline/
  ball.py                  ball tracking + ballistic contact detection + rally grouping  ← core detection
  audio.py                 audio energy envelope + post-rally cheer scoring
  score.py                 highlight scorer (cheer + rally shape + optional CLIP)
  detect.py                YOLOv8-pose classification (refinement + fallback scan)
  clip.py                  FFmpeg clip + thumbnail cutting
  verify.py                CLIP zero-shot action scoring (optional, GPU-friendly)
  ocr.py                   jersey-number OCR (exists, NOT yet wired into the pipeline)
ml/modal_app.py            Modal GPU deployment of track_ball (deploy: modal deploy ml/modal_app.py)

Dockerfile.api             image for api + worker (shared)
Dockerfile.web             image for web
docker-compose.yml         db · redis · api · worker · web  (project name: clipfarm)
.hooks/pre-commit          runs the exact CI checks locally
```

---

## The Processing Pipeline (detail)

The whole app is `process_game_task` in `api/app/workers/tasks.py`. Stages:

### 1. Ball tracking → rallies (`ml/pipeline/ball.py`)
The Roboflow RF-DETR ball model runs on sampled frames (~3/sec, fps-aware) to build a
trajectory. Because the raw tracker hops between the game ball, spare balls, and false
positives, the trajectory is **segmented** on implausible motion before anything is read
from it. Gravity is estimated per-video from the coherent segments, and a **contact** is
flagged where the ball's post-velocity deviates from the ballistic (free-flight)
prediction by more than a measured noise floor. Contacts are grouped into rally windows
by time gaps.

- All velocities are **px/second**, so thresholds hold at any source frame rate.
- Ball positions are cached to R2 keyed by `md5(video) + model + sample_rate`, so re-runs
  (re-uploads, pipeline tuning) load in seconds instead of re-tracking (~28–42 min on CPU).

### 2. Highlight scoring → gate (`ml/pipeline/score.py`, `audio.py`)
Each rally gets a `highlight_score` (0–1) from **cheer** (crowd/bench reaction in the
audio window after the last contact, normalized to the video's p99) plus **rally shape**
(contact count, duration, max ball speed, sharp direction changes, floor bounce). Rallies
below `highlight_score_threshold` (0.50) are dropped here — this is the precision gate, so
pose and clip-cutting only run on rallies worth keeping.

### 3. Pose refinement (`ml/pipeline/detect.py`)
YOLOv8-pose runs **only inside surviving rally windows** and overrides the
trajectory-derived action label when it is more confident.

### 4–5. Weighting + cutting (`ml/pipeline/clip.py`, `_sync_db.py`)
Audio adjusts label confidence, then FFmpeg cuts each clip + thumbnail, uploads to R2, and
writes `Clip` rows. Re-running a game clears its prior clips first (idempotent).

---

## Key Concepts (read these before changing detection)

- **Primary path is ball tracking, not pose.** Pose is a refinement + rare fallback. The
  ball model finds *where the action is*; pose only labels *what kind* within those windows.
- **Contacts are ballistic residuals, not angle/speed thresholds.** At coarse sampling,
  gravity alone bends the trajectory enough to fool a raw angle threshold. Detection
  measures deviation from predicted free-flight instead. See the tuned constants at the top
  of `ball.py`.
- **Cheer is the strongest highlight signal but it is court-blind.** A loud reaction to a
  *failure* (net serve on set point) or a *neighboring court* scores just as high. Ranking
  quality and multi-court false positives are known open problems (see backlog).
- **The ball cache is content-addressed.** Re-uploading the same file is nearly free.
  Changing the model or sample rate invalidates it automatically (it's in the key).
- **Model weights persist in a volume**, not the image — the worker mounts a `model_cache`
  volume so RF-DETR/YOLO weights aren't re-downloaded on every container recreate.

---

## Local Development

Docker Compose is the standard path — it runs the whole stack (db, redis, api, worker, web).

```bash
# 1. Fill credentials
cp .env.docker.example .env.docker      # then edit: R2, Supabase, Roboflow, (optional) Modal

# 2. Bring it up
docker compose --env-file .env.docker up --build
#   web  → http://localhost:3000
#   api  → http://localhost:8000  (docs at /docs)

# 3. Enable the pre-commit hook (runs most of what CI runs — not api/tests)
git config core.hooksPath .hooks
```

Tests:

```bash
# Installs, once per worktree. The pip line supplies pytest for BOTH suites
# below — it is pinned in api/requirements-dev.txt and not in requirements.txt.
npm ci                                   # at the repo root
pip install -r api/requirements-dev.txt  # runtime deps + test-only deps

npm run test --workspace=web             # vitest, ~1s
npm run test:watch --workspace=web       # same suite, watch mode
python -m pytest ml/tests/               # ml eval metrics + dead-time
cd api && python -m pytest tests/        # api (CI runs this; the hook does not)
```

The api suite needs `requirements-dev.txt`, not just `requirements.txt`. Several
tests guard their imports with `pytest.importorskip`, so without the test-only
deps (`fakeredis[lua]` and friends) they **skip silently** — the run still reports
green while covering less than you think. `requirements-dev.txt` pulls the runtime
deps in via `-r requirements.txt`, so it is the only install you need. Test-only
packages deliberately stay out of `requirements.txt`, which builds the production
image via `Dockerfile.api`.

Notes:
- **Default `DATABASE_URL` is shared Supabase** (`.env.docker.example` Option A).
  This is intentional, not an oversight: the team logs into the frontend with a
  shared dev account specifically so everyone uploads to and sees the same games
  and clips. Use this for general dev/testing.
- **Switch to the local `db` container (Option B) whenever you're doing
  schema/migration work.** The api container runs `alembic upgrade head` on every
  boot — on the shared DB that means any container boot (yours or a teammate's)
  can silently advance the shared schema out from under everyone else, which is
  exactly what caused the 007→008 mismatch. If your branch adds or edits an
  Alembic revision, point `DATABASE_URL` at the local db container while you
  iterate, then switch back once you're done.
- **Landing a new migration on Supabase is a deliberate, one-time step**, not a
  side effect of `docker compose up`. When a PR with a new Alembic revision merges,
  one person runs `alembic upgrade head` against Supabase once (point `DATABASE_URL`
  at the pooler, run it, switch back). Don't rely on the next person's container
  boot to do it — coordinate it instead.
- `docker compose restart` does **not** reload env files. To pick up `.env.docker` changes:
  `docker compose up -d --force-recreate <service>`.
- The worker mounts `./api` and `./ml`, so Python changes are picked up on worker restart.
- Modal GPU is optional. Without `MODAL_TOKEN_*`, ball tracking runs on local CPU
  (~28–42 min/game). With Modal deployed (`modal deploy ml/modal_app.py`) + tokens set, it
  runs on a T4 in a few minutes.

---

## Configuration

All settings live in `api/app/config.py` (env-driven, prefixed to match). The most load-bearing:

| Setting | Default | Purpose |
|---|---|---|
| `highlight_score_threshold` | `0.50` | Rallies below this are dropped before pose/cutting. Env-overridable, no rebuild. |
| `clip_verify_enabled` | `false` | Use CLIP frames in scoring (slow on CPU; enable for GPU). |
| `pose_model` / `pose_imgsz` / `pose_skip_frames` | `yolov8s-pose.pt` / `1280` / `4` | Pose quality vs speed. Docker dev overrides to lighter values. |
| `database_url` | — | Supabase Postgres (async). |
| `supabase_url` / `supabase_service_role_key` | — | Auth (JWKS) verification. |
| `r2_*` | — | Cloudflare R2 bucket + credentials. |
| `redis_url` / `celery_*` | redis:6379 | Job queue. |
| `modal_token_id` / `modal_token_secret` | "" | Enables the Modal GPU tracking path. |
| `max_upload_bytes` | 2 GB | Upload size cap. Served to the web app by `GET /games/upload-config` so the advertised limit can't drift from the enforced one. |
| `single_put_max_bytes` / `upload_part_size_bytes` | 100 MiB / 100 MiB | Below the threshold an upload is one presigned PUT; above it, multipart with parts this size. |
| `upload_url_ttl_seconds` | `21600` (6 h) | Lifetime of a presigned upload URL — must cover a whole upload on a slow uplink. |
| `abandoned_upload_hours` | `24` | Upload tickets never completed are swept (row deleted, multipart aborted) on the owner's next presign. |
| `max_upload_duration_seconds` | `14400` (4 h) | Longest single video accepted. Checked at presign against the client-declared length, then again by the worker against the probed one. |
| `quota_window_hours` / `quota_max_games_per_window` / `quota_max_minutes_per_window` | `24` / `5` / `360` | Per-user rolling processing quota (cost guardrail — GPU inference is ~$0.25 per hour of footage). Both caps apply. Counted from the `upload_events` ledger, so deleting a game does not refund a slot. An upload declaring no duration is charged the full `max_upload_duration_seconds` and settled down to the probed value by the worker. `GET /games/upload-config` returns the limits plus the caller's remaining allowance. |
| `raw_upload_retention_days` | `7` | Intended raw-upload TTL (enforcement is a backlog item). |

Secrets go in `.env.docker` (gitignored) for the stack, `api/.env` and `web/.env.local` for
local non-Docker runs. Never commit credentials.

### R2 bucket setup (required for uploads to work)

The browser PUTs video directly to R2, so the bucket must allow it — without these
two settings uploads fail in the browser with an opaque CORS error, no matter how
the api is configured.

**CORS policy** — `ExposeHeaders: ETag` is not optional: multipart completion sends
each part's ETag back to the api, and a cross-origin response header is unreadable
to JavaScript unless it is exposed.

```json
[{
  "AllowedOrigins": ["https://your-web-domain", "http://localhost:3000"],
  "AllowedMethods": ["PUT", "GET", "HEAD"],
  "AllowedHeaders": ["content-type"],
  "ExposeHeaders": ["ETag"],
  "MaxAgeSeconds": 3600
}]
```

**Lifecycle rule** — abort incomplete multipart uploads after ~7 days. The api aborts
on delete and on the abandoned-upload sweep; this is the backstop for uploads neither
path reaches. Parts of an unfinished upload are billed until aborted and are invisible
to a normal object listing.

---

## Data Model

Postgres via SQLAlchemy (`api/app/models/`), RLS enabled on all tables.

| Model | Notes |
|---|---|
| `User` | Supabase-authenticated user (owner of teams/games). |
| `Team` / `Player` | Roster. `Player.team_id` links to a team. (Teams CRUD UI is a backlog item.) |
| `Game` | An uploaded VOD + processing status (`processing` → `ready`/`failed`). |
| `Clip` | A cut highlight: time window, `action_type`, `confidence`, `highlight_score`, R2 URLs, `player_id`. |
| `Collection` | User-curated groupings of clips. |
| `Correction` | User relabel events (written on relabel; readable via `GET /corrections` + CSV `/corrections/export` — training signal). |
| `DeadTimeRun` / `DeadTimeClip` | Separate experimental dead-time detection flow. |
| `UploadEvent` | Append-only quota ledger (CF-91). One row per accepted upload, holding the seconds charged against the per-user window. Deliberately not derived from `Game`: games are hard-deleted, so counting them let a user refund a slot whose GPU cost was already spent. `game_id` is `ON DELETE SET NULL`. |

---

## Conventions & Workflow

We work like a small company: **branches + PRs only, never commit to `main`.**

- **Branch naming**: `category/CF-##-short-description` (lowercase-kebab category).
  Categories in use: `devops`, `ball-detection`, `docs`. `CF-##` is the board card ID.
- **PRs**: use the template (`.github/pull_request_template.md`). One card ≈ one PR. Link
  `CF-##`. Squash on merge.
- **CI** (`.github/workflows/ci.yml`) runs on every PR and is **required to pass** before
  merge (branch protection on `main`): `Web (lint + typecheck)` and `API (ruff + mypy)`.
  The `.hooks/pre-commit` hook runs the exact same checks locally (ruff, mypy, eslint, tsc).
- **Backlog** lives on the `ClipFarmVB` GitHub Project (#1) as `CF-##` cards, each
  backed by an issue titled `CF-## · Description`. Link one from a PR with a bare
  `Closes #<issue>` — `**Board:** CF-##` alone is not parsed by GitHub, so the
  issue stays open and the card never moves.
- **Agent conventions** live in `CLAUDE.md` at the repo root.

---

## Infrastructure & External Services

| Service | Role |
|---|---|
| **Supabase** | Postgres (data) + Auth (JWKS JWT). Free tier **auto-pauses after ~7 days idle** (kept alive by the keepalive workflow — see gotchas). |
| **Cloudflare R2** | Object storage; zero egress cost (important — this is a video app). `ball-cache/` holds cached trajectories. |
| **Roboflow `inference`** | Local RF-DETR ball model `volleyball-ball-tracking-0eo7r/3`. Pinned to `inference==1.3.3`. |
| **Modal** | Serverless T4 GPU for ball tracking. App `clipfarm-ball-tracking`; deploy `modal deploy ml/modal_app.py`. |
| **Redis** | Celery broker + result backend. |
| **Render** | Production hosting for web + api + worker + the Key Value broker, defined as code in `render.yaml`. |

**Deploying:** production is Render — see **[`DEPLOY_RENDER.md`](./DEPLOY_RENDER.md)**
(CF-68) for the Blueprint setup, secrets, domains and the deploy procedure.
[`DEPLOY.md`](./DEPLOY.md) (CF-41) is a separate, optional runbook for self-hosting
the backend on a VPS — an alternative, not the production path.

---

## Known Limitations / Gotchas

- **Supabase free-tier auto-pause**: after ~7 days idle the pooler returns "tenant not
  found" and all processing fails. Unpausing takes ~2 min to propagate. Mitigated by the
  `Supabase keepalive` workflow (`.github/workflows/keepalive.yml`, CF-18), which runs
  `SELECT 1` every 2 days to keep the project active — it needs the `SUPABASE_DB_URL`
  repo secret and only fires from `main`. Note the keepalive only *prevents* a pause; it
  can't *cure* one — if the project is already paused the run just fails red, so unpause
  it manually (Supabase dashboard) and the next scheduled run goes green. The permanent
  fix is upgrading to Supabase Pro (no auto-pause; see CF-68).
- **Multi-court false positives**: footage with several simultaneous games produces junk
  clips from neighboring courts (ball *and* audio). Court-ROI filtering is the top open
  detection task.
- **Scoring ranks poorly even though the gate works**: cheer can't distinguish a great
  rally from a loud non-rally (set-win celebration, funny error). The 0.50 gate is solid;
  ordering within survivors is not. A learned ranker (from user feedback) is the plan.
- **Jersey OCR (`ocr.py`) is not wired in** — every clip's `player_id` is currently null.
- **`inference` must stay pinned** (`==1.3.3`): an unpinned resolver backtracks to a
  version predating RF-DETR support and the ball model silently fails to load.
- **Celery worker runs `--pool=solo`**: one job at a time; a hung transfer blocks the queue
  (mitigated by boto3 socket timeouts). Not horizontally scaled yet.

---

## Where to Look First

- **Understand the app** → this file, then `api/app/workers/tasks.py` (`process_game_task`).
- **Change detection** → `ml/pipeline/ball.py` (constants at top are heavily tuned — read
  the comments before touching).
- **Change scoring** → `ml/pipeline/score.py` + `ml/pipeline/audio.py`.
- **Add an API endpoint** → `api/app/routers/` + wire in `api/app/main.py`.
- **Change the frontend** → `web/` (⚠ read `web/AGENTS.md` first).
- **Every configurable knob** → `api/app/config.py`.
