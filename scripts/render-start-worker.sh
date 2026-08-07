#!/bin/sh
# Render start command for clipfarm-worker. See render-start-api.sh for why
# this is a file and not an inline `sh -c "..."` in render.yaml (CF-170).
#
# --pool=solo: one task per instance. The pipeline is CPU/GPU-bound and the box
# is sized for a single game at a time; prefork concurrency is CF-65b.
set -e

export SENTRY_RELEASE="${SENTRY_RELEASE:-$RENDER_GIT_COMMIT}"

# exec so celery is PID 1 and gets SIGTERM directly — it needs the signal to
# finish the in-flight task rather than being killed mid-clip.
exec celery -A app.workers.celery_app worker --loglevel=info --pool=solo
