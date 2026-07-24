# Model evaluation harness (CF-55 / CF-56)

A fixed benchmark for answering one question objectively: **is the clip model
getting better or worse across versions?**

It is *not* a clipper. It takes a video the pipeline has already processed, plus
a human's hand-labeled highlights for that same video, and scores how closely
the model's clips match the human's — as a small set of threshold-free numbers.
Because the video and labels never change, the **delta between two tagged runs**
is attributable to the model change. Absolute values matter far less than the
trend.

## The four signals

Computed by `metrics.py` (pure interval arithmetic — no matching rules, no
arbitrary overlap cliffs). Let `H` = union of human-labeled intervals,
`M` = union of all model windows, `W` = a single model window.

| Signal | Meaning | Better |
|---|---|---|
| **Captured %** | `|M ∩ H| / |H|` — share of human highlight seconds the model covered | higher |
| **Play buckets** | per human clip, capture fraction `f = |h ∩ M| / |h|`; counts of **well-captured** (`f ≥ 0.5`) / **butchered** (`0 < f < 0.5`) / **missed** (`f = 0`) | more well |
| **Incorrect time** | model seconds *outside* the highlights, split into **junk** (windows touching no highlight), **lead slop** / **tail slop** (before/after the clips a window hits), **bridge** (uncovered gap between two highlights one window spans) | lower |
| **Score AUC** | P(a highlight-overlapping window scores above a junk window), using `highlight_score` | higher (0.5 = blind) |

The only threshold anywhere is the `0.5` well-captured bucket — a *reporting*
cut over already-computed fractions, not part of the measurement.

### Pre-gate vs post-gate — read both
Every run reports the signals twice:
- **pre-gate** — all detected rallies, *before* `highlight_score_threshold`.
- **post-gate** — only the clips that survived the gate.

The pair localizes a regression:
- Coverage lost **pre-gate** → a **detection** problem (the rally was never found). Lowering the threshold won't help.
- Coverage lost **only post-gate** → a **scoring/threshold** problem (found but gated out).

These need opposite fixes, so always compare the two.

### Interpretation notes
- **Lead/tail slop** is *expected* near the design pads (`PRE_RALLY_PAD ≈ 2s`,
  `POST_PLAY_PAD ≈ 2.5s` in `ml/pipeline/ball.py`) — the pads are deliberate.
  The signal to watch is slop *beyond* them (e.g. tail slop averaging ~8s/clip
  is the dead-tail regression class).
- **Junk time** is the multi-court / false-positive tracer.
- **AUC = 0.5** means `highlight_score` can't tell highlights from junk; it's the
  metric that moves when scoring weights are tuned.

## Running it

Both modes need a *processed* game (the model's clips must exist). Run from the
repo root; the `--offline` mode must run where the pipeline deps live (the
worker container).

```bash
# Offline: replay detection + scoring from the R2 ball-cache (no re-tracking).
docker compose exec -e GIT_COMMIT=$(git rev-parse --short HEAD) worker \
  python -m ml.eval.harness --test test1 --version my-change --offline

# Clips-json: score a pre-dumped {pre_gate, post_gate} window list.
python -m ml.eval.harness --test test1 --version my-change --clips-json dump.json
```

- `--version <label>` is free text; it and a `config_snapshot` (pad/score
  constants) + git commit are written with each run.
- `--no-record` prints without appending to the results log.
- Results append one JSON line per run to `results/{test_id}.jsonl` (committed
  to git — that file *is* the cross-version history).

`--clips-json` input shape:
```json
{
  "pre_gate":  [{"start": "01:30", "end": "02:01", "highlight_score": 0.7}],
  "post_gate": [{"start": "01:30", "end": "02:01", "highlight_score": 0.7}]
}
```

## Adding a new test case

1. Label the raw video per the protocol in **CF-56** — the essentials:
   - Label from the **raw video, never the app's clips** (labeling off model
     output makes coverage circular — the model can't be penalized for plays it
     never surfaced).
   - Mark **tight action spans** (first meaningful touch → point end), *not*
     padded clip boundaries. The harness measures slop drift against the pads.
   - One labeler per fixture, named in the file.
2. Drop a `fixtures/{test_id}.json` file (format below). Adding a case needs
   **no code change**.
3. Run the harness with `--test {test_id}` to record its baseline.

### Fixture format — `fixtures/{test_id}.json`
```json
{
  "test_id": "test1",
  "labeler": "Kunyuan",
  "labeling": "independent",
  "source_video_md5": "<content MD5 — matches the ball-cache key>",
  "source_r2_key": "raw/<game_id>.mp4",
  "video_duration_sec": 3660.0,
  "ground_truth_tiers": ["M", "C"],
  "clips": [
    { "start": "03:00", "end": "03:06", "tier": "M", "note": "spike" }
  ]
}
```
`start`/`end` accept `mm:ss`, `hh:mm:ss`, or raw seconds. Pin the video by
**content MD5**, not `game_id` — game rows get deleted; the hash identifies the
exact bytes forever and matches the ball-cache key.

## Files
```
metrics.py    pure signal math (unit-tested in ml/tests/test_eval_metrics.py)
harness.py    fixture load, model-clip acquisition, report, results append
fixtures/     one JSON per test case (ground truth)
results/      {test_id}.jsonl — one row per tagged run, committed
```
