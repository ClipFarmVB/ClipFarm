# ClipFarm — Architecture & Design Decisions

A reference for the system's structure and the reasoning behind its key choices.
Written to double as interview prep: each decision lists the trade-off that drove it.

## What it does

Users upload full volleyball game recordings. The system automatically finds rallies,
classifies actions (spike/serve/dig/set/block), scores highlight-worthiness, cuts
per-rally clips, and — opt-in — produces one condensed video with the dead time
between rallies removed.

## System overview

```
Browser (Next.js 16, React 19)
   │  Supabase JWT in Authorization header            video bytes (presigned PUT)
   ▼                                                             │
FastAPI (async, SQLAlchemy 2.0 + asyncpg) ──► Postgres (Supabase)│
   │  enqueue                                   ▲                │
   ▼                                            │ sync engine    │
Celery worker (Redis broker, solo pool) ────────┘                │
   │  ML pipeline (ml/pipeline/*)                                │
   ▼                                                             ▼
Cloudflare R2 (S3 API): raw videos, clips, thumbs, condensed videos, ball cache
```

- **Uploads bypass the api entirely.** `POST /games/uploads` validates the declared
  type and size and returns presigned URLs; the browser PUTs to R2; `POST
  /games/{id}/uploads/complete` HEADs the object and only then enqueues the job.
  The api handling multi-GB bodies was both a resource cost and a hard blocker for
  serverless hosting (Modal caps request bodies at 4 GiB). It also moves the
  enforcement point *before* the transfer instead of after it.

- **Monorepo**: `api/` (FastAPI + Celery), `ml/` (pure pipeline code), `web/` (Next.js
  app-router).
- **Docker Compose dev stack**: `db` (unused local PG), `redis`, `api`, `worker`
  (same image as api), `web`. Code is volume-mounted so containers hot-reload without
  rebuilds; only dependency changes need `--build`.

## The processing pipeline (`process_game_task`)

One Celery task, imperative stages, each stage wrapped in try/except so a failing
enhancement degrades instead of killing the run:

| Stage | What | Why it's ordered here |
|---|---|---|
| 0 | Audio RMS envelope (ffmpeg → numpy) | Computed once (~seconds), reused by stages 2 and 4 |
| 1 | Ball tracking (Roboflow model) → contacts → rally windows | Primary signal: physics-based, occlusion-tolerant, no GPU |
| 2 | Highlight scoring (cheer + rally shape [+ CLIP]) → drop low scorers | Precision gate *before* expensive pose so pose only runs on keepers |
| 3 | YOLOv8-pose inside surviving windows → refine action labels | Pose is the most expensive signal; scope it to windows — and run it on Modal GPU (CF-164) |
| 4 | Audio confidence weighting | Cheap adjustment, no filtering |
| 5 | Cut clips (ffmpeg), upload to R2, persist rows | |
| 6 | *(opt-in)* Condensed dead-time-removed video | See below; uses the **pre-gate** stage-1 signal |

Fallback chain: no `ROBOFLOW_API_KEY` (or ball failure) → pose detection on Modal GPU →
local CPU pose. Each level catches and falls through.

### Key ML design decisions

- **Ball trajectory over pose as the primary signal.** A contact is a deviation from
  ballistic flight: gravity is estimated per-video (median vertical acceleration of
  coherent track segments), then residuals above a speed-scaled floor mark hits.
  Rationale: the ball is almost always visible, players occlude each other; physics
  generalizes across camera angles where pose heuristics don't.
- **All velocity thresholds are px/second, not px/frame** (CF-34). Same footage at
  60 fps halves px/frame velocities and silently breaks px/frame thresholds; per-second
  units make tuning portable across sources. Sampling is fps-aware too
  (`sample_every = round(fps / 3)` → ~3 detections/sec at any frame rate).
- **Content-addressed ball cache on R2.** Tracking a 22-min video costs ~30 min on CPU
  but depends only on (video bytes, model version, sample rate) — so positions are
  cached at `ball-cache/{md5(video)}-{model}-s{rate}.json`. Re-uploads and pipeline
  re-tuning hit the cache in seconds. Classic memoization keyed by content hash, not id.
- **Precision gate before expensive compute** (stage 2 before stage 3): scoring is
  cheap (audio + features already in hand), pose is not. The gate turns a 22-min VOD
  into ~5 min of clip candidates before the expensive model runs.

## Dead-time removal (condense stage)

**Requirement:** "cut out the waiting between rallies" → one condensed video.

- **Detection is free.** The gaps between stage-1 rally windows *are* the dead time,
  so no extra ML or decode pass runs. `ml/pipeline/dead_time.py` regroups the raw ball
  contacts with *coverage* semantics rather than reusing `contacts_to_rallies()`
  (which curates highlights): no 30s clip cap (a long rally stays one span), a
  1-contact minimum (`condense_min_contacts`) where the highlight path instead
  discards anything under `MIN_RALLY_DURATION`, pad → clamp → merge. Pure functions, unit-tested, tunables as
  kwargs (ml/ never imports app config).
- **Pre-gate signal, deliberately.** The condensed video must keep *every* rally,
  not just the ones the highlight gate scored well — so it consumes the stage-1
  output snapshot, not the post-gate survivors.
- **Two builders, one switch** (`condense_mode`, CF-187). `rules` is the
  original contacts + motion-bridge path and stays the fallback: `guarded` runs
  inside a try/except that drops back to it, so a builder that raises degrades the
  condense instead of failing the run. The fallback's scope is narrower than it
  looks — an unknown mode can no longer reach it (`condense_mode` is a `Literal`,
  so a typo fails at Settings construction, i.e. at boot), and both builders come
  from one import, so an import failure skips the condense stage entirely rather
  than falling back. `guarded` is the default — it rejects
  contacts fired over a near-stationary ball, opens windows on sustained motion
  so a rally the contact detector never sees isn't cut outright, and pads
  tighter.
- **Abstaining beats guessing.** When the ball track is too sparse to judge (below
  ~1 usable speed sample/s, measured on fixture test3 at 0.57/s), every builder
  buys dead time by cutting real play. `guarded` detects that and declines: it
  returns the whole video, the stage sees nothing worth trimming, and the game
  ships with clips but no condensed cut. Scoring nothing is better than scoring
  negative, and it is visible in the logs rather than silent.
- **Stitching: per-window re-encode + concat demuxer with `-c copy`.**
  Alternative considered: single `filter_complex` trim/concat graph. Rejected because
  it decodes the discarded footage too, builds a huge filtergraph for many windows,
  and fails atomically. Two-step encodes only kept footage, isolates per-window
  failures (log + skip), and the final stitch is stream-copy (near-free).
  A/V-sync gotchas handled: identical codec params across parts, pinned audio rate
  (`ar=48000` — mixed rates glitch at copy-concat joins), `avoid_negative_ts` per part,
  `+genpts` on the stitch.
- **Opt-in per upload** (checkbox → `condense` form field → task kwarg + `games`
  column). Encoding kept footage is roughly realtime on CPU and the worker pool is
  solo, so the cost is only paid when asked for.
- **Non-fatal by construction:** clips are saved before the condense stage; any
  condense failure logs and the game still goes `ready`. A nice-to-have must never
  hold the core deliverable hostage.
- **Deterministic R2 key** (`condensed/{game_id}.mp4`): task retries overwrite
  instead of orphaning objects — cheap idempotency.

History: this replaced a half-built standalone "dead time" flow (own tables/router/
task/pages, and inverted semantics — it produced clips *of* the dead time). Migration
008 drops those tables; detection now rides the existing pipeline signal instead of a
separate motion pass.

## API & data decisions

- **Async FastAPI, sync Celery, two DB engines.** Request handlers use
  SQLAlchemy async + asyncpg. Celery workers have no event loop, so `_sync_db.py`
  builds a parallel sync engine (`+asyncpg` stripped from the URL). Interview point:
  async frameworks don't reach into background workers; pick per-context.
- **Status is a coarse enum** (`queued → processing → ready | failed`) polled by the
  frontend every 4-10s. No WebSockets/SSE — polling is trivially correct, stateless,
  and fine at this scale; push channels are a scale optimization, not a starting point.
- **Object storage URLs are stored public-form, served presigned.** DB rows hold
  `{r2_public_url}/{key}`; routers convert to time-limited presigned URLs on read.
  Storage stays private; links expire; the DB never holds secrets.
- **Uploads go browser → R2 directly, presigned** (CF-163). The api validates the
  declared type and size, hands back presigned URLs, and confirms the object with a
  HEAD before enqueueing — so the limit is enforced *before* the transfer rather
  than while proxying it, and a failed upload never becomes a job. Content type is
  signed into the URL, so R2 rejects a PUT whose Content-Type *header* differs —
  but that binds the header, not the bytes, so the HEAD is followed by a ranged
  read that checks the stored object really is a video container (CF-244). Size
  cannot be signed at all (S3/R2 ignore Content-Length as a query parameter),
  which is why the HEAD checks it against the real object.
  Files over 100 MiB use multipart — not for the 5 GiB single-PUT ceiling, which the
  100 MiB threshold keeps out of reach, but so a dropped connection retries one part
  instead of the whole file. XHR (not fetch) on the frontend for upload progress.
- **Auth:** Supabase issues JWTs; the API verifies them (JWKS), never handles
  passwords. Next.js middleware guards routes server-side.
- **Migrations:** Alembic. The api container's start command applies them automatically
  *only against a local database* (`scripts/auto_migrate.py`, CF-189); a shared or
  production target is migrated deliberately instead — Render's `preDeployCommand`, or
  a person running `alembic upgrade head` once. Destructive drops use `IF EXISTS`
  because dev databases drift (008 hit a missing index that 005 claimed to create).

## Operational decisions

- **No ML runtime in the server image at all** (CF-164). This started as
  "CPU-only torch" (`--index-url .../whl/cpu`) to avoid shipping CUDA, then went
  further: with ball tracking (CF-11) and pose (CF-164) both on Modal, torch,
  ultralytics, transformers and Roboflow `inference` leave the image entirely.
  What that buys is, because pose now has a GPU, the full-quality config
  (`yolov8s-pose` @ 1280) that a 2 GB CPU box could not afford — the image was
  sized by its heaviest import, not its workload. It briefly bought a `starter`
  (512 MB) Render worker too, but CF-240 put that back to `standard` (2 GB):
  what sizes this box now is `libx264` during cutting, not an import.

  It also removes two long-standing build hazards along with the packages that
  caused them, both worth remembering if an ML dependency is ever added back:
  `inference` had to be **pinned to 1.3.3** (CF-33) because pip's resolver
  backtracked to a version predating the ball model's architecture — resolver
  backtracking can "succeed" into a broken state; and its transitive `zxing-cpp`
  has no arm64 wheel, so Apple Silicon builds compiled it from source behind a
  same-layer `build-essential`/`cmake` install-and-purge.

  The cost is that a Modal outage no longer degrades into a slow local run. The
  local code paths all still exist and still work against `ml/requirements.txt`;
  they are simply not installed in the deployed image.
- **boto3 socket timeouts** (CF-32): with a solo worker pool, one hung S3 transfer
  blocks the entire queue; connect/read timeouts + retries turn hangs into retryable
  failures.
- **Known trade-off:** the solo Celery pool serializes jobs — one long condense/track
  blocks the queue. Accepted for dev; the scale-out path is more workers or a
  dedicated queue, not threads (the work is CPU/ffmpeg-bound).

## Testing strategy

- **Pure logic gets unit tests** (`ml/tests/test_intervals.py`,
  `ml/tests/test_dead_time.py`): interval math, grouping, padding/clamping —
  fast, no video, no models.
- **ffmpeg/cv2 behavior gets harness scripts**: run against real or synthetic
  footage locally in a personal scratch dir (git-ignored `ball-track/`);
  assertions on durations/streams, human eyeballs on output. Model behavior
  isn't unit-testable; cached tracking fixtures keep iterations fast and free.
- **End-to-end**: docker compose stack + real upload through the UI.
