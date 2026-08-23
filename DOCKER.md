# Docker Quickstart

This setup runs the full local stack with one command:
- PostgreSQL
- Redis
- FastAPI API
- Celery worker — **capped at production's box** (2 GB / 1 CPU / no swap,
  `FFMPEG_THREADS=1`), so a laptop can reproduce a production OOM (CF-241)
- Next.js web app

A sixth service, `eval`, is profile-gated and never starts with the stack — see
[step 4](#4-going-faster-or-smaller) and `ml/eval/README.md`.

## 1) Create env file

From repo root:

```bash
cp .env.docker.example .env.docker
```

Fill required values in `.env.docker`:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `JWT_SECRET`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_PUBLIC_URL`

You can leave `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` blank if not using Modal.

`DATABASE_URL` defaults to the local `db` container (Option A in the template).
Switch to the shared Supabase pooler (Option B) when you want the team's shared
games and clips — see the migration note under "Useful commands" before you do.

The R2 bucket also needs a **CORS policy** before uploads work at all — the browser
PUTs video straight to R2 and never routes it through the api. It must allow `PUT`
from `http://localhost:3000` and expose the `ETag` header (multipart completion
cannot read it otherwise). See "R2 bucket setup" in `README.md` for the exact policy.

## 2) Start everything

```bash
docker compose --env-file .env.docker up --build
```

Open:
- Web: http://localhost:3000
- API health: http://localhost:8000/health

Note: Postgres is not published to a host port to avoid `5432` conflicts.
Use `docker compose exec db psql -U postgres -d clipfarm` if you need a DB shell.

## 4) Going faster, or smaller

The worker runs at production's size by default, which is the point — but it
also means a local game processes at production's speed. Both directions are one
prefixed command:

```bash
WORKER_MEM_LIMIT=8g WORKER_CPUS=4 WORKER_FFMPEG_THREADS=4 docker compose up worker
```
```bash
WORKER_MEM_LIMIT=512m WORKER_CPUS=0.5 WORKER_FFMPEG_THREADS=4 docker compose up worker
```

The first is the fast path for "does the pipeline work" — it costs the fidelity
the defaults exist for, so no timing or memory claim may come from such a run.
The second reproduces the CF-224 OOM.

CPU-bound work that is *not* about production's box — the eval harness, the
tuning scripts — goes on the unconstrained `eval` service instead:

```bash
docker compose run --rm --no-deps eval python -m ml.eval.harness --help
```

Full detail in `README.md` § Local Development.

## 5) Stop everything

```bash
docker compose down
```

To also remove Postgres data volume:

```bash
docker compose down -v
```

## Useful commands

Show service status:

```bash
docker compose ps
```

Tail worker logs:

```bash
docker compose logs -f worker
```

Run migrations manually:

```bash
docker compose exec api alembic upgrade head
```

That command is how a **non-local** `DATABASE_URL` gets migrated. On boot the api
runs `scripts/auto_migrate.py`, which applies `alembic upgrade head` only when the
configured database is on a local host (`LOCAL_HOSTS` in that script — the `db`
service and the loopback addresses); against the shared Supabase pooler it skips
and logs why, so `docker compose up` cannot advance the shared schema on its own
(CF-189).

The one other way in is `ALEMBIC_ALLOW_REMOTE=1`, which opts a boot back into
migrating a remote host. Nothing in the repo sets it; don't, unless you mean it.
