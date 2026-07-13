<!-- title: CF-49 · Dedicated fast queue for interactive jobs -->
<!-- labels: devops, feat, P1 -->
**Epic:** Virtual clips over a mezzanine proxy.

Add a second Celery queue + worker so short user-facing jobs never wait behind a 40-min `process_game` (the solo-pool worker serves one job at a time today). Route `recut_clip` now and `materialize_clip` (CF-50) to a `fast` queue via `task_routes` in `api/app/workers/celery_app.py`; `process_game`/`process_dead_time` stay on default. Add a second worker service in `docker-compose.yml` (`celery ... worker -Q fast --pool=solo`, same image/env/model-cache volume).

**Acceptance:** enqueue a `process_game`, then a `recut` — the recut completes while the game is still processing. Both workers visible in logs; README service list updated.

**Depends:** none. Parallel-safe with CF-48.
