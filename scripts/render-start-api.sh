#!/bin/sh
# Render start command for clipfarm-api.
#
# This lives in a file rather than inline in render.yaml's `dockerCommand`
# because that value is not tokenized the way a shell would tokenize it: a
# quoted body arrives as a single *word*, so `sh -c "VAR=x cmd args"` runs a
# shell that then looks for one command by that entire name, and the service
# dies with
#   sh: 1: SENTRY_RELEASE=<sha> uvicorn app.main:app ...: not found
# which reads like a missing binary and is not (CF-170). The `sh: 1:` prefix is
# dash's own error format — a shell did run; it was handed an unsplit string.
#
# Two things need a shell, so a shell is what runs them:
#   PORT            - assigned by Render per instance; uvicorn needs it as an arg.
#   RENDER_GIT_COMMIT - injected by Render; ties Sentry events to the deployed
#                     commit so a regression traces to a specific deploy.
set -e

# Respect an explicitly set SENTRY_RELEASE; otherwise fall back to the deploy's
# commit. Blank is safe either way - observability.py passes `or None`.
export SENTRY_RELEASE="${SENTRY_RELEASE:-$RENDER_GIT_COMMIT}"

# exec so uvicorn is PID 1 and receives Render's SIGTERM directly; without it
# the shell holds the signal and the platform falls back to SIGKILL.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
