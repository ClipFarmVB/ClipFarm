# Self-hosted backend on a VPS (CF-41) — *alternative*, not the production path

> **Production is [`DEPLOY_RENDER.md`](./DEPLOY_RENDER.md) (CF-68).** ClipFarm
> deploys the full stack (web + api + worker + broker) on Render as managed
> infrastructure. This runbook is kept as a **self-hosted alternative** — a
> Render escape hatch, and a cheap always-on box for long-running batch work
> (reprocessing footage, the CF-55 eval harness).
>
> **Don't use it as a staging environment.** Staging is only useful with parity,
> and this differs from Render prod in every deployment-shaped dimension:
> runtime (compose vs Render services), secrets (plaintext `.env.docker` vs a
> secret store), migration timing (a manual step vs `preDeployCommand`), TLS, and Key
> Value persistence. It would pass while prod fails and vice versa. A real
> staging environment is **CF-152**, as a second service group in the Render
> Blueprint.
>
> Not exercised by CI — treat it as best-effort and expect some drift.

This runbook stands up the ClipFarm **backend** — redis + api + worker — on a
small always-on VPS so video processing keeps running when your dev machine is
off. It was written (CF-41) as the get-off-the-laptop step before a full
production deploy existed.

> **Scope:** backend only. The Next.js frontend (`web/`) can stay on Vercel or
> run separately — it is not required on this box. The database stays on
> Supabase; only redis + the Celery worker + the FastAPI enqueue path move here.

---

## 0. Prerequisites (read first)

- **Modal must be configured.** Since CF-164 this is a hard requirement rather
  than a speed knob: ball tracking *and* pose both run on Modal's T4 GPU, and
  the image no longer ships torch to fall back to. Without
  `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` the box still cuts clips, but every
  game comes out with trajectory-only labels — and if the ball model is
  unreachable too, the game fails outright rather than inventing clips. Deploy
  both Modal apps first if you haven't:

  ```bash
  modal deploy ml/modal_app.py && modal deploy ml/modal_pose.py
  ```
- Credentials on hand for `.env.docker`: Supabase (`DATABASE_URL` pooler string,
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`), Cloudflare R2 (`R2_*`), Roboflow
  (`ROBOFLOW_API_KEY`), Modal (`MODAL_TOKEN_*`), and a `JWT_SECRET`.
- SSH access to a fresh Ubuntu 22.04/24.04 host.

### Box sizing

| Resource | Recommendation | Why |
|---|---|---|
| vCPU | 2 | ffmpeg cutting + OpenCV frame work, one game at a time |
| RAM | 2–4 GB | redis + api + worker together; no torch since CF-164 |
| Disk | 40–80 GB | 2 GB upload cap × transient working files (no model cache) |

A **Hetzner CPX21 (3 vCPU / 4 GB, ~€8/mo)** or a DigitalOcean / EC2 equivalent
is plenty; the older 8 GB recommendation here was sized for torch. No GPU on
this box — Modal owns every model.

> This asks for more RAM than the Render worker's `starter` (512 MB) plan, which
> is not a contradiction: **this box runs all three services in one place**
> (redis, api and the worker), where Render splits them across instances. For
> the worker process alone the peak is the ~150 MB audio energy envelope; see
> the headroom note in `render.yaml`.

---

## 1. Provision the host

Create the VPS (Ubuntu LTS), then SSH in and install Docker + the compose
plugin:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"    # log out/in so this applies
docker compose version             # sanity check the plugin is present
```

> **⚠ Compose v2.24+ is required — verify before deploying.** `docker-compose.prod.yml`
> relies on the `!override` merge tag. On older Compose the tag is **not applied
> and the file still parses**, so the local `db` dependency and the source
> bind-mounts silently leak back into the production stack. Gate on it explicitly:
>
> ```bash
> docker compose version --short | awk -F. '{ if ($1 < 2 || ($1 == 2 && $2 < 24)) { print "FAIL: Compose " $0 " < 2.24 — !override unsupported, prod overrides would silently not apply"; exit 1 } else print "OK: Compose " $0 }'
> ```
>
> If this fails, upgrade the Compose plugin before continuing.

Docker's systemd service is enabled on boot by default, so the stack (with
`restart: unless-stopped`) comes back after a reboot.

## 2. Harden networking

Lock the box down to SSH only. Redis and the internal services never need a
public port.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
# Only if the API must be reachable directly (see the TLS note in §7 first):
# sudo ufw allow 8000/tcp
sudo ufw enable
```

## 3. Clone the repo

```bash
git clone https://github.com/ClipFarmVB/ClipFarm.git
cd ClipFarm
```

## 4. Create `.env.docker`

Copy the template and fill in **real** values. `.env.docker` is gitignored —
never commit it.

```bash
cp .env.docker.example .env.docker
nano .env.docker
chmod 600 .env.docker            # readable only by your user
```

Must be set for this deploy:

- `DATABASE_URL` → the Supabase **pooler** connection string (`postgresql+asyncpg://…`).
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `JWT_SECRET`.
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_PUBLIC_URL`.
- `ROBOFLOW_API_KEY`.
- **`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`** — the prerequisite from §0.

> Secrets-on-disk is the minimum for CF-41. Productionizing secret handling
> (no plaintext `.env` files) is tracked separately in **CF-90 (#90)** and
> supersedes this step once done.

## 5. Bring up the backend

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build redis api worker
```

This builds the shared api/worker image on the box, starts redis (private),
the API, and the Celery worker (`--pool=solo`, one job at a time).

> **⚠ Migrations do not run here.** `DATABASE_URL` on this box points at
> Supabase, and the api's boot guard (`scripts/auto_migrate.py`, CF-189) refuses
> to migrate a non-local host — so booting this box no longer advances the shared
> schema as a side effect. When the branch you deploy carries a new revision,
> apply it deliberately, once, before or right after bringing the stack up:
>
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.prod.yml exec api alembic upgrade head
> ```
>
> Coordinate it with the team, per the "shared DB" warning in the README.
>
> CF-68 (#98) made migrations a deliberate step on the Render path via
> `preDeployCommand`; CF-189 closes the same hole here, from the other side — by
> refusing rather than by scheduling. The paths still differ in *timing*: Render
> applies them automatically once per deploy, this box waits for the command
> above. That remains a parity gap, just no longer a silent one.

## 6. Verify end-to-end

```bash
# Services healthy?
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f worker

# API reachable on the box?
curl -s localhost:8000/health          # {"status":"ok"}
```

Then the real test:

1. Upload a game (from the frontend pointed at this API, or via the upload
   endpoint) and watch it move `queued → processing → ready`.
2. Confirm both GPU stages ran **on Modal** — the dashboard should show a
   `clipfarm-ball-tracking` and a `clipfarm-pose` invocation, and the worker log
   should say *"Ball tracking ran on Modal GPU"* and *"Pose refinement ran on
   Modal GPU"*, **not** a *"falling back to local CPU"* line.
3. Confirm clips + thumbnails appear in R2 and `Clip` rows in Supabase.
4. **Close your laptop.** Upload again (or re-enqueue). It should still process
   — that is the whole point of CF-41.

## 7. Operations

**Tail logs**
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f worker api
```

**Deploy an update**
```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build redis api worker
```

**Restart / stop**
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart worker
docker compose -f docker-compose.yml -f docker-compose.prod.yml down    # stop all
```

**TLS / public exposure.** This runbook leaves the API on plain `:8000`
behind the firewall. Do **not** expose it to the internet without TLS — front
it with a reverse proxy (Caddy/nginx/Cloudflare Tunnel) terminating HTTPS.
Domains, TLS and a proper edge are handled for you on the production path
(**CF-68**, Render) — this box is deliberately firewalled instead.

---

## Known limitations & related tickets

- **Pose now runs full-quality, on Modal (CF-164).** This box used to pin
  `yolov8n-pose @ 640, every 8th frame` in `docker-compose.prod.yml` because
  pose was the one model still running on its CPU. Pose moved to the GPU, so
  that block is gone and `app/config.py`'s defaults (`yolov8s-pose @ 1280`,
  every 4th frame) are what runs — better labels *and* less local work.

  **What it costs:** a Modal outage no longer degrades gracefully into a slow
  local run, because the image has no torch. Clips are still cut, from ball
  contacts; only the action labels suffer. Re-adding a CPU path means installing
  `ml/requirements.txt` into the image **and** sizing the box back up — doing
  one without the other just makes processing slow or OOM.

- **One game at a time.** Celery runs `--pool=solo` / `prefetch=1`; a second
  upload queues behind the first. Concurrency + horizontal scaling is **CF-65**.
- **Secrets live in `.env.docker` on the box.** Hardened secret management is
  **CF-90 (#90)** — still open, and the main reason this path isn't production.
- **Observability — already solved.** CF-89 (#89) shipped Sentry across api,
  worker and web; it is wired by env var (`SENTRY_DSN`), so it applies to this
  box too. Set the DSN in `.env.docker` — and set `SENTRY_ENVIRONMENT` to
  something distinct (e.g. `self-hosted`), because the template ships
  `development` and this box would otherwise report into the same bucket as
  every teammate's compose stack, which is the one place you actually want to
  filter on. Then worker errors report like anywhere else.
- **Supabase auto-pause.** The worker depends on Supabase; the keepalive from
  **CF-18** already guards against the free-tier idle pause.
- **This is not CF-68's backend tier.** It was written expecting to be, but CF-68
  went with Render (`render.yaml` + [`DEPLOY_RENDER.md`](./DEPLOY_RENDER.md)),
  which provisions its own managed services — nothing in this runbook is reused
  there. The two are alternative hosting strategies; production is Render.
