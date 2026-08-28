# Docker Quickstart

This setup runs the full local stack with one command:
- PostgreSQL
- Redis
- FastAPI API
- Celery worker — **capped at production's box** (2 GB / 1 CPU / no swap,
  `FFMPEG_THREADS=1`), so a laptop can reproduce a production OOM (CF-241)
- Next.js web app

A sixth service, `eval`, is profile-gated and never starts with the stack — see
[step 3](#3-going-faster-or-smaller) and `ml/eval/README.md`.

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

Note: Postgres **is** published, on `${POSTGRES_HOST_PORT:-5432}` — tests and
tools outside the stack need to reach it (`api/tests`' advisory-lock tests
auto-detect it and skip without one). If 5432 is already taken by a Postgres on
your host, set `POSTGRES_HOST_PORT=5433` in `.env.docker`; that is interpolated,
so it only applies to commands passing `--env-file .env.docker`. For a shell
inside the container, `docker compose exec db psql -U postgres -d clipfarm`.

## 3) Going faster, or smaller

The worker runs at production's size by default, which is the point — but it
also means a local game processes at production's speed. Both directions are an
overlay file, not a remembered env-var prefix:

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.fast.yml up worker
```
```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.repro.yml up worker
```

If `docker compose` refuses the fast overlay with `range of CPUs is from 0.01
to N.00`, your engine has fewer than 4 — check `docker info --format
'{{.NCPU}}'` and lower it. **Lower the thread count with it**: 4 x264 threads on
2 CPUs is CF-224's oversubscription in miniature, costing memory and buying
nothing.

```bash
WORKER_CPUS=2 WORKER_FFMPEG_THREADS=2 WORKER_MEM_LIMIT=4g docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.fast.yml up worker
```

The first is the fast path for "does the pipeline work". It costs the fidelity
the defaults exist for, so **no timing or memory claim may come from such a
run** — the rule is written in the file. The second reproduces the CF-224 OOM,
and a run under it that *completes* is the interesting result.

**Keep `--env-file .env.docker`** on every one of these, exactly as in step 2.
It is what Compose interpolates `${...}` from. Without it, `POSTGRES_HOST_PORT`
and friends come from a `./.env` if you happen to have one and from the built-in
defaults otherwise — either way not from `.env.docker`, and the db container
tries to bind 5432 whatever that file says.

CPU-bound work that is *not* about production's box — the eval harness, the
tuning scripts — goes on the unconstrained `eval` service instead:

```bash
docker compose --env-file .env.docker run --rm --no-deps eval python -m ml.eval.harness --help
```

Full detail in `README.md` § Local Development.

## 4) Stop everything

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
