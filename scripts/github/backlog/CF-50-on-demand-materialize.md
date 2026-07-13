<!-- title: CF-50 · On-demand clip materialization + download endpoint -->
<!-- labels: devops, api, feat, P1 -->
**Epic:** Virtual clips over a mezzanine proxy.

Clip files are cut lazily on first download, cached by (clip, start, end), cut from the full-res **original** via HTTP range reads (`ffmpeg -ss` before `-i` against a presigned URL — do NOT download the whole 326MB; measure, fall back to full download only if range reads prove unreliable). New `materialize_clip` task on the `fast` queue. Cache validity via new nullable `Clip.materialized_start` / `materialized_end` columns (+ migration); **backfill** existing clips so today's files become pre-warmed cache. Endpoint `POST /clips/{id}/materialize` → `{status:"ready", url}` if valid else enqueue + `{status:"preparing"}`; idempotent (no duplicate jobs).

**Acceptance:** old clip → instant ready; stale clip → preparing → ready within ~1 min while a game is simultaneously processing (proves CF-49); repeat calls don't duplicate jobs.

**Depends:** CF-49.
