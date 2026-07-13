<!-- title: CF-54 · Proxy epic hardening + cleanup -->
<!-- labels: devops, chore, P2 -->
**Epic:** Virtual clips over a mezzanine proxy. Steady-state cleanup.

(a) R2 hygiene: periodic job / lifecycle rule deleting materialized files whose `materialized_*` no longer match their row (trim-orphans). (b) Metrics: log proxy encode duration, materialize p50/p95, download cache-hit rate. (c) Remove dead code from the always-cut world (old recut references, unused cutting branches — keep the short-cut path materialize uses). (d) Update README + tuning docs for the virtual-clip model. (e) File a follow-up card: proxy encode on Modal GPU (NVENC) + bump default to 1080p30.

**Acceptance:** no orphaned-object growth after repeated trims; metrics in worker logs; README matches reality.

**Depends:** CF-52.
