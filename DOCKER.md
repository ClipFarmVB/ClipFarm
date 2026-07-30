# Docker Quickstart

This setup runs the full local stack with one command:
- PostgreSQL
- Redis
- FastAPI API
- Celery worker
- Next.js web app

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

## 2) Start everything

```bash
docker compose --env-file .env.docker up --build
```

Open:
- Web: http://localhost:3000
- API health: http://localhost:8000/health

Note: Postgres is not published to a host port to avoid `5432` conflicts.
Use `docker compose exec db psql -U postgres -d clipfarm` if you need a DB shell.

## 3) Stop everything

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
