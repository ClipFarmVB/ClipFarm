# Deploying the ClipFarm backend off a laptop (CF-41)

This runbook stands up the ClipFarm **backend** — redis + api + worker — on a
small always-on VPS so video processing keeps running when your dev machine is
off. It is the compute-off-laptop stepping stone to the full production deploy
(CF-68); everything here is reused as CF-68's backend tier.

> **Scope:** backend only. The Next.js frontend (`web/`) can stay on Vercel or
> run separately — it is not required on this box. The database stays on
> Supabase; only redis + the Celery worker + the FastAPI enqueue path move here.

---

## 0. Prerequisites (read first)

- **Modal must be configured.** Ball tracking (~30 min/game) offloads to Modal's
  T4 GPU when `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` are set. With them, this box
  only needs CPU. **Without them, this box does local CPU tracking (~28–42 min)
  and a small VPS will be slower than your laptop.** Deploy the Modal app first
  if you haven't: `modal deploy ml/modal_app.py`.
- Credentials on hand for `.env.docker`: Supabase (`DATABASE_URL` pooler string,
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`), Cloudflare R2 (`R2_*`), Roboflow
  (`ROBOFLOW_API_KEY`), Modal (`MODAL_TOKEN_*`), and a `JWT_SECRET`.
- SSH access to a fresh Ubuntu 22.04/24.04 host.

### Box sizing

| Resource | Recommendation | Why |
|---|---|---|
| vCPU | 2–4 | pose (YOLOv8-pose) + ffmpeg run on CPU, one game at a time |
| RAM | 4–8 GB | torch + inference + OpenCV frames; 8 GB comfortable |
| Disk | 40–80 GB | 2 GB upload cap × transient working files + ~200 MB model cache |

A **Hetzner CPX31 (4 vCPU / 8 GB / 160 GB, ~€15/mo)** or a DigitalOcean /
EC2 equivalent is plenty. No GPU on this box — Modal owns the GPU stage.

---

## 1. Provision the host

Create the VPS (Ubuntu LTS), then SSH in and install Docker + the compose
plugin:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"    # log out/in so this applies
docker compose version             # sanity check the plugin is present
```

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
> (no plaintext `.env` files) is tracked separately in **CF-77** and supersedes
> this step once done.

## 5. Bring up the backend

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build redis api worker
```

This builds the shared api/worker image on the box, starts redis (private),
the API (which runs `alembic upgrade head` then uvicorn), and the Celery
worker (`--pool=solo`, one job at a time).

> **⚠ Migration coordination.** The API runs `alembic upgrade head` against
> Supabase on every boot. Supabase is the shared database — booting this box
> can advance the shared schema. Make sure the branch you deploy is at the
> intended migration head and coordinate with the team, per the "shared DB"
> warning in the README. (CF-68 will turn migrations into a deliberate deploy
> step rather than a boot side effect.)

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
2. Confirm tracking ran **on Modal** — check the Modal dashboard for a
   `clipfarm-ball-tracking` invocation, and the worker log should say
   *"Ball tracking ran on Modal GPU"*, **not** a local-CPU tracking run.
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
Wiring a domain + TLS + a proper edge is **CF-68**, not CF-41.

---

## Known limitations & related tickets

- **One game at a time.** Celery runs `--pool=solo` / `prefetch=1`; a second
  upload queues behind the first. Concurrency + horizontal scaling is **CF-65**.
- **Secrets live in `.env.docker` on the box.** Hardened secret management is
  **CF-77**.
- **Observability.** Worker error tracking + uptime alerting lands via **CF-76**
  and slots straight into this box.
- **Supabase auto-pause.** The worker depends on Supabase; the keepalive from
  **CF-18** already guards against the free-tier idle pause.
- **This is CF-68's backend tier.** The full production deploy (domains, TLS,
  CI/CD image builds, deliberate migrations, monitoring) builds on top of this.
