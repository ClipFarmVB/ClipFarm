# Scale-to-Zero — evaluation and decision

> **Status: evaluated, decided.** Epic **CF-161** is closed. This records what was
> considered, what we're doing, and — more usefully — **what we decided not to do and
> why**, so it isn't re-litigated in six months.
>
> Current production architecture remains [`DEPLOY_RENDER.md`](./DEPLOY_RENDER.md).

---

## What prompted it

Production costs **$49.25/month flat** — web $7 + api $7 + worker $25 + Key Value $10 —
and the worker is idle well over 99% of the time at current usage.

Render has **no scale-to-zero for paid services**: the minimum is one always-on instance.
So autoscaling cannot help — with `min=1` it returns to the same $49.25 at idle and only
adds spend when the queue is deep. It's a floor problem, not a ceiling problem.

The question asked was: could the stack bill per *game processed* rather than per *hour
rented*?

## The answer, and why it isn't the whole answer

Technically yes. Modal hosts full ASGI apps, so the api could scale to zero; jobs could
spawn as Modal functions; Celery and Redis could be deleted. Idle would land near $0.

**But the objective changed once the numbers were on the table.** The original goal was
minimum idle cost. The actual goal turned out to be *the most appropriate stack*, with a
small-to-medium monthly bill being perfectly acceptable. Roughly 80% of the case for the
biggest step was cost — so it does not survive that reframing.

What follows is what stands up **without** the cost argument.

---

## Doing

### CF-163 — presigned direct-to-R2 uploads
The browser uploads straight to R2; the api issues a presigned URL and records metadata,
never touching the bytes.

Never really a cost item. It removes the api from the multi-GB data path, fixes the
CF-167 cap mismatch properly, eliminates a class of upload timeout/retry failures, and
gives CF-91 a natural enforcement point — limits are decided when the URL is issued,
before any bytes move.

### CF-164 — pose → Modal
Moves YOLOv8-pose to Modal, the pattern `track_ball` already uses.

Pays twice. Removing torch/ultralytics/`inference` from the worker image lets it run on
`starter` (−$18/mo), **and** full-quality pose returns on GPU — `yolov8s` @ 1280 instead
of the light config production ships — which improves CF-3's action labels and retires
the "don't compare prod labels against CF-55 baselines" warning in `DEPLOY_RENDER.md`.

Worth doing for the quality alone.

### CF-184 — Postgres advisory lock replaces the Redis TTL lock
The strongest finding of the whole evaluation, and it turned out **not to require the
migration it arrived bundled with**.

`locks.py` uses Redis `SET NX EX` — a lock whose TTL has no relationship to whether the
holder is alive. `celery_app.py` documents the consequence: a hard-killed worker leaves
the lock held, the requeued task no-ops, and the game strands in `processing`. That is
the entire reason CF-65g (#149) exists.

A session-scoped Postgres advisory lock is released when its connection dies. Kill the
worker, the lock goes with it, the retry succeeds — **the failure mode stops existing
rather than being reaped after the fact.** Celery is untouched; the lock mechanism is
independent of the queue.

---

## Not doing

### CF-165 — web → Cloudflare (closed)
Only ever worth **$7/month**, and the implementation path carries more risk than that buys:

- Cloudflare **no longer recommends Pages** for Next.js; the current path is OpenNext on
  Workers, so the original ticket named a deprecated approach.
- An [active Next.js 16 version trap](https://github.com/cloudflare/workers-sdk/issues/13755)
  between Next 16's proxy architecture and the current Cloudflare adapter.
- **Middleware is the known sharp edge**, and ours runs `supabase.auth.getUser()` on
  nearly every request. That's auth, not a peripheral feature.

Vercel doesn't rescue it: Hobby forbids commercial use, and Pro at ~$20/seat costs *more*
than the Render service it would replace.

**Web stays on Render.** $7/mo for a zero-risk, already-working frontend is a good trade.

### CF-166 — api → Modal, retire Celery (backlog)
With cost set aside, what remains is elastic scaling we don't need yet — Celery prefork
(CF-65b) plus replicas covers moderate concurrency — in exchange for:

- replacing the Alembic mechanism (Modal has no `preDeployCommand` equivalent)
- rewiring Sentry off `CeleryIntegration`
- unverified custom-domain support
- cold starts on a user-facing api
- local dev no longer mirroring production

**Revisit when concurrent load actually exceeds a prefork-pooled worker.** The trigger is
scale, not the bill.

---

## Where that leaves the stack

FastAPI + Celery + Redis + Postgres + R2, with GPU bursts on Modal. Boring, standard,
already understood by the team — and **~$31/month** steady state once CF-164 lands.

The genuinely inappropriate parts were narrow: the api sitting in the upload data path,
and pose running on CPU at reduced quality. CF-163 and CF-164 fix exactly those. The rest
of the architecture was already right.

## Consequences for other cards

- **CF-65c / CF-65d / CF-65f are *not* superseded.** They were briefly marked so while
  CF-166 looked likely. Celery stays, so Celery-based scaling is the live path.
- **CF-65b (PR #154)** goes from "possibly moot" to the right next step for concurrency.
- **CF-65g (#149)** is superseded by **CF-184** rather than by CF-166 — same conclusion,
  much smaller change. Don't build the reaper until CF-184 is decided; if the Supabase
  pooler blocks session-scoped locks, it returns as the fallback.
