<!-- title: CF-48 · Proxy generation in the pipeline -->
<!-- labels: devops, feat, P1 -->
**Epic:** Virtual clips over a mezzanine proxy.

Add a proxy-encode stage to `process_game_task` while the original is still on local disk: encode a streamable full-game copy (H.264/AAC, `-movflags +faststart`, ~1s forced keyframe interval) at a configurable resolution/fps, upload to `proxy/{game_id}.mp4`, store on `Game.proxy_video_url` (nullable column + migration). New config in `api/app/config.py`: `proxy_height=720`, `proxy_fps=30`, `proxy_crf=23`, env-overridable, never upscale past source. Encode itself in `ml/pipeline/clip.py`. Failures non-fatal.

**Acceptance:** processing yields a proxy that starts playing in a browser within ~1s and seeks near-instantly; `GET /games/{id}` returns a presigned `proxy_video_url`; games without proxies return null and nothing breaks. Benchmark encode time on CPU in the PR; note GPU/NVENC as follow-up if >15 min.

**Depends:** none. Parallel-safe with CF-49.

⚠ Migration: coordinate order, merge before running against shared Supabase (see CONTRIBUTING.md).
