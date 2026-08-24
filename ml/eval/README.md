# Model evaluation harness (CF-55 / CF-56 / CF-98)

A fixed benchmark for answering one question objectively: **is the clip model
getting better or worse across versions?**

Two modes, two different questions, two separate fixtures:

| Mode | Question | Fixture | Docs |
|---|---|---|---|
| default | are the **highlights** it clips the right ones? | `fixtures/{test_id}.json` | this file |
| `--mode deadtime` | does **condense** cut dead time without cutting play? | `fixtures/{test_id}_deadtime.json` | [`fixtures/README_deadtime.md`](fixtures/README_deadtime.md) |

**Working on condense / dead-time?** Read
[`fixtures/README_deadtime.md`](fixtures/README_deadtime.md) first — the tier
semantics differ from the highlight fixture in a way that silently inverts the
metric if you get it wrong (see [Adding a new test case](#adding-a-new-test-case)).

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
repo root; the `--offline` mode must run where the pipeline deps live — the
**`eval` service**, which is the worker's image without the worker's resource
limits (CF-241). Use `worker` only when what you are measuring is the production
box itself (encode timings, memory headroom); a correctness sweep held to 1 CPU
is just a slower sweep.

`eval` is profile-gated, so `docker compose up` never starts it and every
invocation below is a one-shot `run --rm`. Keep the `--env-file .env.docker`:
Compose interpolates `${...}` from it, and without it the stack's ports and
limits silently fall back to their defaults. It has no default command — a bare
`run eval` prints usage and exits 64 rather than starting a stray uvicorn. It
pins no `FFMPEG_THREADS` and no broker URLs, because neither is read on an eval
path: `Settings.ffmpeg_threads` reaches only `recut_clip_task` and
`process_game_task`, and nothing here imports `ml.pipeline.clip`. Pinning either
would be config that looks load-bearing and is not.

Nothing in the repo hands `eval` the queue — which is not the same as it being
unable to reach one. `eval` still loads your `.env.docker`, so a
`CELERY_BROKER_URL` set there does arrive, and `run --rm eval celery ...` would
then take real jobs and run them unconstrained. Don't.

Not the same as "eval never runs ffmpeg": `--offline` calls
`compute_audio_energy`, which shells out to ffmpeg to pull mono PCM
(`ml/pipeline/audio.py`). That is a `-vn` decode, not CF-224's x264 encode, and
it reads no thread setting — but it is a real subprocess, so measure with it in
mind.

```bash
# Offline: replay detection + scoring from the R2 ball-cache (no re-tracking).
docker compose --env-file .env.docker run --rm --no-deps -e GIT_COMMIT=$(git rev-parse --short HEAD) \
  eval python -m ml.eval.harness --test test1 --version my-change --offline

# Clips-json: score a pre-dumped {pre_gate, post_gate} window list.
python -m ml.eval.harness --test test1 --version my-change --clips-json dump.json

# Dead-time: score a dumped condense keep-window list. No video, no app deps —
# runs on a laptop.
python -m ml.eval.harness --mode deadtime --test test1 --version my-change \
  --windows-json keep.json

# Dead-time, offline: derive the windows from the real video via the R2
# ball-cache, mirroring the pipeline's stage-5 condense path.
docker compose --env-file .env.docker run --rm --no-deps -e GIT_COMMIT=$(git rev-parse --short HEAD) \
  eval python -m ml.eval.harness --mode deadtime --test test1 \
  --version my-change --offline
```

- `--version <label>` is free text; it and a `config_snapshot` (pad/score
  constants) + git commit are written with each run.
- `--no-record` prints without appending to the results log.
- Results append one JSON line per run to `results/{test_id}.jsonl` — or
  `results/{test_id}_deadtime.jsonl` in dead-time mode (both committed to git —
  those files *are* the cross-version history).
- Dead-time extras: `--audit-limit N` caps each divergence list (0 = all), and
  `--dump-windows` on an `--offline` run saves the derived windows so a later
  re-score needs no container and no download.

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

### …for dead-time mode

A dead-time case is a **separate label pass** into
`fixtures/{test_id}_deadtime.json` — full format in
[`fixtures/README_deadtime.md`](fixtures/README_deadtime.md). Do not reuse the
highlight fixture's `clips` list.

The trap worth stating here, because nothing errors when you hit it: `keep_tiers`
selects which tiers count as **ball-in-play**, and that is a wider set than
"highlight-worthy" — `M`/`C`/`N` are all live ball (a failed serve is still play
the condense stage must keep), while only `B` (break) and `O` (camera outlier)
become dead time. Reusing the highlight tiers `["M", "C"]` would count every
boring-but-real rally as dead time, so a model that correctly kept them scores as
*missing dead time* and one that aggressively cut real play scores as *removing
more* — the metric inverts.

`load_deadtime_fixture` is deliberately permissive: an absent `keep_tiers`, or a
span with no tier, counts as in-play. A tier-less fixture therefore loads
without complaint and scores every labeled span as play. Silent, plausible, and
wrong — so copy the dead-time format rather than adapting the highlight one.

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
metrics.py             pure signal math, both modes (unit-tested in ml/tests/)
harness.py             fixture load, model-clip acquisition, report, results append
diagnose_detection.py  why a rally was missed: BLIND / SPARSE / GATED breakdown
tune_contacts.py       sweep find_contacts tunables over a dumped ball track
fixtures/              one JSON per test case (ground truth)
                       {test_id}.json          highlights (CF-55)
                       {test_id}_deadtime.json ball-in-play spans (CF-98)
                       README_deadtime.md      dead-time fixture format
results/               {test_id}.jsonl and {test_id}_deadtime.jsonl —
                       one row per tagged run, committed
```
