# Scale-to-Zero Architecture — proposal

> **Status: proposed, not decided.** Tracked as epic **CF-161**. Step 4 is gated on
> the spike (**CF-162**). This document explains the change; the cards carry the work.
>
> Current production architecture is [`DEPLOY_RENDER.md`](./DEPLOY_RENDER.md).
> Nothing here is live.

---

## The problem

Production costs **$49.25/month flat** — web $7 + api $7 + worker $25 + Key Value $10 —
and at current usage the worker is idle well over 99% of the time.

This is not a capacity problem, so autoscaling cannot fix it. Render has **no
scale-to-zero for paid services**: the minimum is one always-on instance. Autoscaling
with `min=1` returns to the same $49.25 at idle and only adds spend when the queue is
deep, which at a handful of users never happens.

**It's a floor problem, not a ceiling problem.**

## The idea

Change the unit of billing from **time** to **work**.

Today you rent four machines by the hour whether or not a game is processing. After,
you pay per game processed and roughly nothing when idle. You already have one component
that works this way — Modal, which is why the GPU stage costs ~$0.20/game instead of a
monthly instance fee. The proposal extends that model to the rest of the stack.

## Before

```
Browser
    │  uploads video THROUGH the api (multipart)
    ▼
[Render] api ───────────────► R2: raw/{game_id}.mp4
    │  enqueue process_game
    ▼
[Render] Redis   ← Celery broker + result backend
    │
    ▼
[Render] worker  (2 GB, always on, --pool=solo)
    ├─ ball tracking ──► Modal GPU        ← already scale-to-zero
    ├─ pose            (local CPU, light config)
    ├─ scoring + ffmpeg clip cutting
    └─ clips ──► R2 + Postgres

always on:  web $7 + api $7 + worker $25 + redis $10  =  $49.25/mo
```

## After

```
Browser ──presigned PUT──────► R2: raw/{game_id}.mp4   (api never sees the bytes)
    │  POST /games  (metadata only)
    ▼
[Modal] api (FastAPI ASGI)         ← runs only during a request
    │  .spawn()
    ▼
[Modal] process_game               ← one container per job, N in parallel
    ├─ ball tracking   (GPU)
    ├─ pose            (GPU, full quality restored)
    ├─ scoring + ffmpeg
    └─ clips ──► R2 + Postgres  (advisory lock + progress)

[Cloudflare Pages] web             ← free
[Supabase] Postgres                ← free tier

always on:  nothing.   ~$0–5/mo idle
```

---

## What happens to Celery

The obvious objection is that we recently hardened Celery across several PRs (CF-65a
redelivery safety, CF-150 result handling). None of that reasoning is wasted — each
guarantee is **replaced one for one**, not dropped.

| Celery provides | Replaced by |
|---|---|
| the queue | Modal `.spawn()` |
| retries, `task_acks_late`, `reject_on_worker_lost` | Modal function retries + timeouts |
| per-game lock (Redis `SET NX EX`) | **Postgres advisory lock** |
| progress tracking | already Postgres (`Game.status`) — unchanged |
| idempotent clip refresh (CF-37) | unchanged — lives in the DB layer |
| `task_ignore_result` (CF-150) | n/a — no result backend exists |

### The lock change is the point

`api/app/workers/locks.py` uses Redis `SET NX EX` — a lock with a **TTL**, which has no
relationship to whether the holder is still alive. That is the root cause of **CF-65g
(#149)**: kill a worker mid-job, the lock outlives the process, the requeued task finds
it still held and no-ops, and the game strands in `processing` with nothing working on
it. `celery_app.py` documents this in situ.

A Postgres advisory lock is **session-scoped** — it is released when the connection
dies. Kill the job, the lock goes with it, the retry succeeds.

**So #149 is not fixed by this change; it stops existing.** That is a correctness
argument that happens to also be cheaper, and it is the strongest reason to do this.

### Scaling follows the same pattern

Modal runs one container per spawned job and scales out by default. That turns three
open cards into a configuration value:

| Card | Becomes |
|---|---|
| CF-65c — horizontal scaling, multiple replicas | `max_containers` |
| CF-65d — queue-depth autoscaling | not needed; no queue to measure |
| CF-65f — worker observability (Flower) | Modal dashboards + Sentry |

These are flagged as likely superseded on the board, pending the CF-162 spike.

---

## What it costs

Honest trade-offs, not footnotes:

- **Cold starts.** A few seconds on the first request after idle — a real, user-facing
  cost. Warm containers remove it and cost money, which defeats the purpose.
- **Vendor concentration on Modal** (api + jobs + GPU). Genuine blast radius. Mitigated
  by the app being standard ASGI, so it ports to Cloud Run or Fly with little change.
- **Local dev stops mirroring production.** `docker compose` no longer reflects what
  runs in prod; that needs documenting.
- **At high utilization, per-second billing loses** to a flat instance. There is a
  crossover point. Current usage is nowhere near it.
- **Sentry rewiring.** `CeleryIntegration()` no longer applies; job error capture must
  be wired for Modal, preserving the credential scrubbing from CF-89/#131.

---

## The steps, and why they're in this order

| # | Card | Effect | Depends on |
|---|---|---|---|
| 1 | **CF-163** — presigned direct-to-R2 uploads | — | — |
| 2 | **CF-164** — pose → Modal | $49 → ~$31 | — |
| 3 | **CF-165** — web → Cloudflare Pages | ~$31 → ~$24 | — |
| 4 | **CF-166** — api → Modal ASGI, retire Celery + Redis | ~$24 → **~$0–5** | 1, and CF-162 |

**Step 1 is a hard dependency, not a preference.** Modal caps request bodies at 4 GiB
and the UI currently advertises 15 GB. Until the browser uploads straight to R2, the api
physically cannot move. It also resolves CF-167 properly and gives CF-91 a natural
enforcement point — limits are decided when the presigned URL is issued, before any
bytes move.

**Step 2 is worth doing regardless of the rest.** Removing torch/ultralytics/`inference`
from the worker image lets it run on `starter`, *and* restores full-quality pose on GPU
(`yolov8s` @ 1280 instead of the light config production ships). Cheaper and better
simultaneously — and the quality half improves CF-3's action labels as a side effect.

**Step 3 is independent** and can be taken at any time. Cloudflare Pages rather than
Vercel: Vercel's Hobby tier forbids commercial use, and Pro at ~$20/seat costs more than
the $7 it saves.

**Step 4 is the commitment**, gated on CF-162 answering three questions:

1. Custom domains on Modal web endpoints — undocumented; may force Cloudflare in front
   or Cloud Run instead.
2. What replaces `preDeployCommand` for Alembic. Modal has no equivalent hook, and
   deleting the Render api deletes the migration mechanism.
3. The advisory-lock design against Supabase's pooler — transaction-mode pooling and
   session-scoped locks do not mix.

---

## The decision

Steps 1–3 are unambiguous wins in any world; they reduce cost, fix a real bug, and
improve output quality without architectural risk.

Step 4 is a genuine project with genuine unknowns. The spike exists so that it is
decided on evidence rather than enthusiasm — and so that CF-65c/d/f are not built on a
premise the team is planning to change.
