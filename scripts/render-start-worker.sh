#!/bin/sh
# Render start command for clipfarm-worker. See render-start-api.sh for why
# this is a file and not an inline `sh -c "..."` in render.yaml (CF-170).
#
# --pool=prefork with N children (CF-65b): N games in parallel, each in its own
# forked process. N comes from CELERY_WORKER_CONCURRENCY (app/config.py), NOT a
# flag here — it defaults to 1, which is the same throughput the solo pool gave,
# so this deploy changes nothing until someone raises it deliberately. Size it by
# MEMORY, not cores: each child decodes its own video. Per-child post-fork setup
# is app/workers/forksafe.py.
set -e

export SENTRY_RELEASE="${SENTRY_RELEASE:-$RENDER_GIT_COMMIT}"

# exec so celery is PID 1 and gets SIGTERM directly — it needs the signal to
# finish the in-flight task rather than being killed mid-clip.
exec celery -A app.workers.celery_app worker --loglevel=info --pool=prefork
