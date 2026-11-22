#!/bin/sh
# Render start command for clipfarm-worker. See render-start-api.sh for why
# this is a file and not an inline `sh -c "..."` in render.yaml (CF-170).
#
# Pool choice follows CELERY_WORKER_CONCURRENCY (app/config.py, default 1):
#
# - N = 1 (the default): --pool=solo, exactly as before CF-65b. Prefork with one
#   child is NOT equivalent. It recycles the child every
#   CELERY_MAX_TASKS_PER_CHILD tasks, keeps a resident parent on the same box,
#   and when the OOM killer takes the child, task_reject_on_worker_lost requeues
#   the game at once, with nothing counting attempts, so a game that OOMs on
#   every attempt (CF-224) can hold the only slot indefinitely. Under solo the
#   OOM kills the whole process and the message only comes back after the
#   visibility timeout.
# - N > 1: --pool=prefork with N children (CF-65b): N games in parallel, each in
#   its own forked process, with the requeue behaviour above. Size N by MEMORY,
#   not cores: each child decodes its own video. Per-child post-fork setup is
#   app/workers/forksafe.py. Raising N is a deliberate production change.
set -e

export SENTRY_RELEASE="${SENTRY_RELEASE:-$RENDER_GIT_COMMIT}"

POOL=solo
N="${CELERY_WORKER_CONCURRENCY:-1}"
case "$N" in
  ''|*[!0-9]*) ;;  # not a plain integer: stay on solo
  *) if [ "$N" -gt 1 ]; then POOL=prefork; fi ;;
esac

# exec so celery is PID 1 and gets SIGTERM directly — it needs the signal to
# finish the in-flight task rather than being killed mid-clip.
exec celery -A app.workers.celery_app worker --loglevel=info --pool="$POOL"
