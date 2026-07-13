<!-- title: CF-51 · Virtual-clip player (invisible mode) -->
<!-- labels: web, feat, P1 -->
**Epic:** Virtual clips over a mezzanine proxy.

Player component that plays `(proxy_url, start, end)` and looks/behaves exactly like today's file player. When a clip's game has `proxy_video_url`: load proxy, seek to `start` (wait for `seeked`/`canplay` before playing to avoid frame-0 flash), pause when the playhead crosses `end` (requestAnimationFrame watcher, not `timeupdate`, for a tight stop), replay re-seeks to start. **Scrubber clamped to the clip window in this ticket — invisible mode; roaming comes in CF-53.** No proxy (old games) → fall back to the existing `clip_url` file player; both paths coexist. Touches `ClipModal.tsx`, `ClipCard.tsx`, `web/src/lib/api.ts`.

**Acceptance:** with a proxy, play/pause/replay is indistinguishable from today side-by-side; without a proxy, byte-identical to current `main`.

**Depends:** CF-48. Parallel-safe with CF-50.
