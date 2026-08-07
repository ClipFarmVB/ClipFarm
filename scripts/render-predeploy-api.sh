#!/bin/sh
# Render pre-deploy for clipfarm-api: apply migrations once per deploy, before
# any new instance starts. Not on every container boot — replicas would race
# the schema (see render.yaml).
#
# A script for the same reason as the start commands (CF-170): a quoted body in
# render.yaml arrives at the shell as a single word, so
#   preDeployCommand: "sh -lc 'cd /app/api && alembic upgrade head'"
# risks failing the whole deploy before the start script is ever reached.
#
# The `cd` is kept rather than relying on the image's final WORKDIR: alembic
# resolves alembic.ini from the working directory, and Render does not document
# the cwd it uses for this hook.
set -e

cd /app/api
exec alembic upgrade head
