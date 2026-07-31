# Production Deployment (Render)

ClipFarm's production stack is defined as code in [`render.yaml`](./render.yaml): a
Next.js **web** service, a FastAPI **api** service, a Celery **worker**, and a
managed **Redis**. Supabase (Postgres + Auth), Cloudflare R2 (storage) and Modal
(GPU) remain external managed services.

This file covers the steps a person must do — the Blueprint handles everything
else. Tracked as **CF-68**; related: CF-18 (Supabase Pro), CF-89 (monitoring),
CF-90 (secret management).

> **Two deploy docs exist — this one is the production path.**
> [`DEPLOY.md`](./DEPLOY.md) (CF-41) stands the **backend** up on a self-managed
> VPS with `docker compose`. It is an **alternative self-hosted option**, not a
> stepping stone to this file and not a staging environment — nothing in it is
> reused here. Keep it as a Render escape hatch and a cheap box for long-running
> batch work (reprocessing, the CF-55 eval harness); treat this file as
> authoritative for anything user-facing.
>
> **Why Render is production:** the VPS path keeps secrets as a plaintext
> `.env.docker` on disk, which is exactly what CF-90 (a launch blocker) exists to
> eliminate; it has no managed TLS, is a single point of failure, and runs
> `alembic upgrade head` on **every boot** — the hazard this Blueprint removes by
> moving migrations to `preDeployCommand`.

---

## One-time setup

### 1. Supabase tier (CF-18)
The free tier auto-pauses after ~7 days idle, which takes the whole app down.
**CF-18 (#78)** mitigates this on the free tier with a scheduled keepalive query,
so Pro is **not strictly required just to avoid the pause** once CF-18 is merged
and its first run is confirmed.

> ⚠️ **Sequencing:** #78 is still open, and the keepalive workflow only fires
> from `main`. Until it merges *and* one manual run is confirmed green, the
> free-tier pause is **unmitigated** — so either merge #78 before the first
> apply, or start on Pro.

Pro (~$25/mo) is still recommended before real users for reasons the keepalive
doesn't cover — **automated daily backups** (free tier has none — the biggest
gap for real user data), higher connection limits, and more compute headroom.
Reasonable path: launch a beta on **free + CF-18 keepalive**, upgrade to Pro when
you have real users or the no-backups risk becomes unacceptable.

### 2. Create the Blueprint
1. Render Dashboard → **New → Blueprint**.
2. Connect the **ClipFarmVB/ClipFarm** repo, branch **`main`**.
3. Render reads `render.yaml` and shows the four services + the `clipfarm-shared`
   env group. Apply.

### 3. Fill the secrets
Every env var marked `sync: false` must be pasted in the dashboard. Sources:

**`clipfarm-shared` group** (used by api + worker):

| Key | Where to find it |
|---|---|
| `DATABASE_URL` | Supabase → Project Settings → Database → **Connection pooler** URI. Format: `postgresql+asyncpg://…`. See pooler note below. |
| `SUPABASE_URL` | Supabase → Project Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | same page — **server-only**, never expose |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Cloudflare → R2 → Manage API Tokens |
| `R2_PUBLIC_URL` | your bucket's public URL (`https://<bucket>.r2.dev` or R2 custom domain) |
| `SENTRY_DSN` | Sentry → **clipfarm-api** project → Settings → Client Keys (DSN). Shared by api + worker (worker events are tagged `service:worker`). Blank = monitoring off. |

**`clipfarm-api`:**
- `API_BASE_URL` → this service's public URL (set after step 4, or its custom domain)
- `CORS_ORIGINS` → the web origin(s), comma-separated, e.g. `https://clipfarm.app`

**`clipfarm-worker`:**
- `ROBOFLOW_API_KEY` → Roboflow → Settings → API Keys
- `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` → modal.com → Settings → API Tokens (leave blank to run inference locally on CPU — slow)

**`clipfarm-web`:**
- `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` → Supabase API page (anon key is browser-safe)
- `NEXT_PUBLIC_API_URL` → the API's public URL, e.g. `https://api.clipfarm.app`
- `NEXT_PUBLIC_SENTRY_DSN` → Sentry → **clipfarm-web** project → Client Keys (a different DSN from the api one)
- `SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN` → optional but recommended: enables source-map upload so prod web stack traces show real source lines instead of minified bundles. Token from Sentry → Settings → Auth Tokens.

> `SENTRY_ENVIRONMENT` / `NEXT_PUBLIC_SENTRY_ENVIRONMENT` are already pinned to
> `production` in `render.yaml`, and the release is set automatically from
> `$RENDER_GIT_COMMIT` — no action needed for either.

> **Ordering:** `NEXT_PUBLIC_API_URL`, `CORS_ORIGINS`, and `API_BASE_URL` reference
> URLs that don't exist until services are created. Easiest path: add the custom
> domains (step 4) first so you know the final URLs, then fill these three, then
> trigger a redeploy of web (its values are baked in at build time).

### 4. Domains + HTTPS
- Add a custom domain to **clipfarm-web** (e.g. `clipfarm.app`) and to
  **clipfarm-api** (e.g. `api.clipfarm.app`) in each service's Settings.
- Point the DNS records at Render (via Cloudflare). Render provisions HTTPS
  automatically.

---

## Verify the deploy
1. `clipfarm-api` → open `/healthz` (shallow liveness — what Render's health
   check watches), expect `{"status":"ok"}`. Once CF-89 (#107) merges, also point
   an **external uptime monitor** at `/health` — the deep check that returns 503
   when Postgres/Redis is down, so it can alert.
2. Load the web domain, sign up (confirm the verification email arrives — needs
   CF-17 custom SMTP for volume; Supabase's built-in sender is rate-limited).
3. Upload a short video → confirm the worker picks it up (worker logs) and clips
   appear. This exercises api → Redis → worker → R2 → Modal end to end.
4. **Model cache disk** — check the worker logs on first job: it should download
   the ball model once, then on a redeploy **not** re-download it. If you see a
   `PermissionError` writing to `/models`, the disk mounted root-owned and the
   non-root worker user can't write it (see the note in `render.yaml`) — fall
   back by pointing the `MODEL_*` vars off the disk, or run the worker as root.
5. Redeploy once and confirm no data loss.

---

## Deploying a change

**Auto-deploy is off on all three services.** Merging to `main` does *not* ship
to production — you deploy deliberately:

1. Merge the batch to `main`.
2. Render Dashboard → the service → **Manual Deploy → Deploy latest commit**.
3. Deploy **`clipfarm-api` first** (its `preDeployCommand` applies migrations),
   then `clipfarm-worker`, then `clipfarm-web`. The worker shares the api's
   models, so shipping it against an un-migrated schema is the thing this
   ordering avoids.

**Why it's off:** the api runs `alembic upgrade head` on every deploy. With
auto-deploy on, any merge to `main` would apply a migration to the production
database unattended — the same class of failure as the 007→008 crash-loop, one
environment over, and at odds with `CONTRIBUTING.md` treating migrations as a
coordinated step.

**This is deliberately the substitute for a staging environment.** There isn't
one yet, and manual deploys buy the same protection for migrations at zero cost:
a human is the gate. Note the kanban's "Staging" column is a *workflow state*
("merged, awaiting deploy"), not an environment — it keeps working as-is. A real
staging environment is **CF-152**, to be added as a second service group in this
same Blueprint when there are real users; that's also what would let
`autoDeploy` come back on (`clipfarm-web` first, it's the safest).

---

## Notes & gotchas

- **Migrations run once per deploy** via the api service's `preDeployCommand`
  (`alembic upgrade head`), not on every boot. Don't re-add per-boot migration to
  production start commands — it races across restarts and can advance a shared
  schema unexpectedly.
- **Supabase pooler + asyncpg:** the transaction-mode pooler doesn't support
  prepared statements. If you hit `prepared statement already exists`, use the
  session-mode pooler port or append the appropriate asyncpg options. (The dev
  stack already runs against the pooler, so the working URL format carries over.)
- **Worker sizing** is the main cost lever. It ships on `standard` (2 GB) with
  the **light** `POSE_*` values (`yolov8n-pose` @ 640) — the config the dev stack
  has actually exercised. `app/config.py`'s defaults (`yolov8s-pose` @ 1280) are
  higher quality but unproven on a 2 GB box; raise the env values once you've
  measured real headroom, rather than discovering an OOM on day one. Better
  long-term: offload more of the pipeline to Modal (CF-65) so the box stays small.
- ⚠️ **Production runs the light pose config, so its action labels are weaker
  than the `config.py` defaults.** Clip *boundaries* come from ball tracking and
  are unaffected, but per-clip action labels (dig/set/spike — the subject of
  CF-3) will be worse than a run using the defaults. **Don't compare production
  label quality against CF-55's eval baselines** unless the eval was run with the
  same `POSE_*` values — they'd disagree for configuration reasons, not model
  ones. Raise the prod values to match before drawing any conclusion.
- **Region:** everything is pinned to `oregon`. Confirm the Supabase project sits
  in (or nearest to) that region — every DB round-trip pays the difference, and
  CF-47's progress writes made the worker chattier, so a cross-region mismatch
  would show up there first.
- **Broker and result backend share one Key Value instance in prod.** The dev
  compose splits them by database index (`/0` broker, `/1` results); Render hands
  out a single `connectionString`, so both land in the same instance. Functionally
  fine (Celery namespaces its keys), but result metadata then accumulates
  alongside the queue — and `noeviction` is set precisely so a *full* instance
  errors loudly rather than dropping jobs. Nothing in `api/` ever reads a result
  (no `AsyncResult`/`.get()`; progress is polled from Postgres), so those writes
  are pure overhead. Tracked separately — see the `task_ignore_result` follow-up.
- **Queued jobs survive a broker restart — but only on a paid plan.** Free Key
  Value instances have **no persistence**, so a restart would silently drop every
  queued game (and, once CF-65a's `acks_late` lands, in-flight ones too). Paid
  plans persist by default (journal + snapshot, ~1s of writes at risk). This is
  why `render.yaml` pins `plan: starter` and not `free`. Note that *upgrading*
  the instance type requires a restart and can itself lose data depending on the
  persistence mode — drain the queue first.
- **Redis is the broker** — `maxmemoryPolicy: noeviction` is set so a full
  instance surfaces an error rather than silently dropping queued jobs.

## Security (CF-90)
- No production secret should live in a file in this repo — everything is in
  Render's secret store or the `clipfarm-shared` group.
- **Rotate any credential that has ever been shared in plaintext** (chat, tickets,
  screenshots) before go-live — notably the Modal tokens and the Supabase DB
  password. Set the fresh values in Render, not in `.env` files.
