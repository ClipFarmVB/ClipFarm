<!-- title: CF-52 · The flip: pipeline stops cutting clips; trim = DB write -->
<!-- labels: api, feat, P1 -->
**Epic:** Virtual clips over a mezzanine proxy. **The cutover — single carefully-reviewed PR, only after CF-48/49/50/51 all merged + verified.**

In `process_game_task`: remove the per-clip cut/upload loop — write the clip row + thumbnail only. `Clip.clip_url` becomes nullable for new clips (now = materialized cache file from CF-50). Rewrite trim (`PATCH` start/end): validate, write row, **invalidate cache** (delete stale R2 object best-effort); delete `recut_clip_task` + its enqueue — nothing re-encodes on trim. Wire download/share buttons to CF-50's materialize endpoint with a minimal "Preparing…" poll. Optional: pre-materialize top-N clips by score. **Do not delete existing R2 clip files** — they're cache.

**Acceptance:** freshly processed game → rows + thumbnails + proxy, zero clip files; clips play (CF-51), trim instantly (no worker job), download via materialize (preparing→ready works); old games unaffected.

**Depends:** CF-48, CF-49, CF-50, CF-51.
