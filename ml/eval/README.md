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
repo root; the `--offline` mode must run where the pipeline deps live (the
worker container).

```bash
# Offline: replay detection + scoring from the R2 ball-cache (no re-tracking).
docker compose exec -e GIT_COMMIT=$(git rev-parse --short HEAD) worker \
  python -m ml.eval.harness --test test1 --version my-change --offline

# Clips-json: score a pre-dumped {pre_gate, post_gate} window list.
python -m ml.eval.harness --test test1 --version my-change --clips-json dump.json

# Dead-time: score a dumped condense keep-window list. No video, no app deps —
# runs on a laptop.
python -m ml.eval.harness --mode deadtime --test test1 --version my-change \
  --windows-json keep.json

# Dead-time, offline: derive the windows from the real video via the R2
# ball-cache, mirroring the pipeline's stage-5 condense path.
docker compose run --rm --no-deps -e GIT_COMMIT=$(git rev-parse --short HEAD) \
  worker python -m ml.eval.harness --mode deadtime --test test1 \
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

## Which condense builder runs (CF-187)

`condense_mode` in `app.config` picks the keep-window builder, and every mode
lives in `ml/pipeline/dead_time.py`:

| mode | builder | notes |
|---|---|---|
| `rules` | `active_windows_from_contacts` + `bridge_windows_by_motion` | the CF-46 path; still the fallback when `guarded` raises |
| `guarded` | `active_windows_guarded` | **the default.** Speed-gated contacts, motion anchors, tight pads, and an abstain when the ball track is too sparse |

The guarded path became the default on the fixture numbers below, not on a
clean sweep — it buys dead time with live play on two of them, and that trade is
the thing to look at before touching its tunables:

| fixture | dead removed | live cut | |
|---|---|---|---|
| test1* | 56.2% → 56.5% | 176s → **116s** | strictly better |
| test2 | 9.5% → **51.1%** | 2s → 12s | +10s of play for 41 points of dead time |
| test3* | 76.5% → 0.0% | 118s → **0s** | abstains rather than cut 118s of rally |
| test4 | 44.2% → **70.9%** | 83s → **66s** | strictly better |
| test5* | 4.6% → **52.6%** | 0s → 18s | +18s of play for 48 points of dead time |

`* = held out while the variants were tuned.` Strictly better on two, a paid
trade on two, an abstain on one. At the 4:1 live-cut exchange rate the harness
and trainer share, that nets +1568s against `rules`' +615s — but the two paid
trades are real, and live play is the axis this repo protects, so a change that
widens them needs more than a better net.

**Abstaining is a real outcome, not a failure.** Below
`condense_guard_min_track_rate` usable speed samples/s the builder returns one
whole-video window; the condense stage sees that nothing meaningful would be
trimmed and ships the game with no condensed cut at all. A 0.0% dead-removed row
in the harness means this, and the offline runner prints `ABSTAINED` so it can't
be mistaken for a broken run.

## Comparing condense variants (CF-187)

`deadtime_variants.py` holds the ladder that produced the guarded path. Its two
endpoints ship — `v0` is `mode=rules`, `v5` is `mode=guarded` — and both call
`ml/pipeline/dead_time.py` rather than reimplementing it, so a row here is
production's behaviour and not a lookalike. `v1`–`v4` are the intermediate rungs,
kept so the next change can be judged against them. `visualize_deadtime.py`
scores them all against every dead-time fixture and writes a standalone HTML
timeline.

```bash
python -m ml.eval.visualize_deadtime   # -> results/deadtime_visualization.html
```

The HTML is **gitignored** — it is ~1MB of inline SVG that would re-churn its
whole diff on every run. Regenerate it rather than looking for it in git, and
note that doing so needs the ball caches (also gitignored, `ml/eval/ball_caches/`):
a fresh clone cannot rebuild it until those are in place.

Two things to read carefully, because both invert a number's meaning:

- **The padding ceiling** shown per game is the most dead time removable at the
  `rules` pads with *zero* live cut. It bounds `v0`–`v3`. It does **not** bound
  `v4`/`v5`, which shrink the pads to 3/2 with merge 3 — an 8s budget against the
  14s one — so they clear it legitimately rather than by cutting play.
- **A variant can beat the ceiling by cutting real play.** `v4` removes 90.6% of
  test3's dead time against a 23.2% ceiling by cutting 162s of rally. Read the
  dead-removed column against the live-cut column, never alone.

`TUNED_ON` marks which fixtures the variants were designed against (test2 and
test4); every other column is held out and is starred in the summary. test5 is
the strongest of those — it was labeled after the variants were written, so it
could not have shaped them even indirectly.

## The pose signal for condense (CF-198)

`v6`-`v8` add a player-activity signal to the guarded builder. It answers a
question the ball signal structurally cannot — *is anyone moving like they are
playing* — which is what CF-187's two residual failure modes both turn on: a
rally the ball detector never sees still has players swinging, and a contact
fired over a spare ball has nobody swinging at all.

`pose_activity_samples` in `dead_time.py` produces the same `(times, values)`
shape as `speed_samples`, so `speed_gate_contacts` and `motion_anchor_windows`
apply to it unchanged — pose gets a gate and an anchor with no second
implementation of either.

**The unit is body-heights/s, not the frame-heights/s of every ball speed.**
Wrist travel is divided by each player's own bounding-box height, so a far-court
swing and a near-court one read the same. The two units are not interchangeable
and a threshold moved between them will silently mean something else.

### Building the caches

Pose inference is the expensive part and the fixtures never change, so it is
cached per video like the ball tracks, keyed by `source_video_md5` and
gitignored:

```bash
python -m ml.eval.build_pose_cache --all --device mps   # or --device cuda
```

Raw keypoints are cached rather than a derived scalar: occupancy geometry and
player-count are the obvious next features, and deriving those from a cache is
free while re-running inference is a GPU pass per idea.

**test1 cannot be built.** Its source video was deleted from R2 after labeling,
and the fixture pins content by MD5, so the YouTube original would not be the
same bytes. `load_game` yields a game with no poses, the pose rungs raise
`PoseUnavailable`, and the runner prints `— not scored` for those cells. That is
deliberate: with an empty activity series those builders would score *identically
to v5*, which reads as "pose changes nothing here" and is indistinguishable from
a measurement. A four-fixture total is not comparable to a five-fixture one, so
the summary marks the rung `(4/5)`.

### Results

Scored on test2-test5. `*` = held out; thresholds were tuned on test2 + test4
only, against net seconds rather than AUC.

| rung | test2 | test3* | test4 | test5* | tuned | held-out* | live cut |
|---|---|---|---|---|---|---|---|
| `v5` guarded (default) | 51% / -12s | abstains | 71% / -66s | 53% / -18s | +545s | +121s | 97s |
| `v6` + pose gate | 61% / -14s | abstains | 81% / -81s | 61% / -42s | **+614s** | **+58s** | 137s |
| `v7` + pose anchor | 49% / -12s | abstains | 59% / -21s | 49% / -15s | +613s | **+124s** | **48s** |
| `v8` + both | 58% / -14s | abstains | 67% / -27s | 58% / -36s | **+693s** | **+72s** | 77s |

Cells are `dead removed % / live play cut`. Deltas against `v5`:

| rung | tuned | held-out | live cut |
|---|---|---|---|
| `v6` | +68s | **-63s** | +40s |
| `v7` | +68s | +3s | **-49s** |
| `v8` | +147s | **-50s** | -20s |

**The gate does not generalize and the anchor does.** `v6` is the best rung on
the two fixtures it was tuned on and the worst on the two it was not, while
cutting 40s *more* live play — a textbook overfit, and `v8`'s large tuned gain is
that same overfit carried along. Read the tuned column alone and `v8` looks like
the winner; it is the held-out column that says otherwise.

`v7`'s held-out net gain of +3s is noise, and the honest claim is not that it
raises net. It is that it reaches the same net while cutting **half as much live
play** (97s → 48s), and on test4 it cuts the loss by two thirds (66s → 21s) by
opening windows over the 19-of-46 rallies that produce no ball contact at all.
Live play is the axis this repo protects, so a change that buys it back at flat
net is worth having.

Read the per-fixture cells before the totals, though: `v7` is flat on test2,
flat on test5, and wins on test4 — the fixture with the worst ball-contact
coverage, and one of the two it was tuned on. That is the mechanism behaving as
designed (pose pays off where contacts fail), but it means the rung rests on one
non-independent game. Tuning can sharpen it; only a second fixture with poor
contact coverage can show the effect is real.

### Tuning the anchor's window shape

The pose anchor reuses `motion_anchor_windows`, whose constants were tuned
against *ball* speeds. Sweeping them against the pose series on test2 + test4
found one change worth making and several worth refusing:

- **`min_seconds` 2.0 → 1.0** (`POSE_ANCHOR_MIN_SECONDS`). Ball speed stays high
  for a whole rally; a dig or a swing is well under a second, so a 2s minimum run
  discarded the bursts this signal exists to catch. Worth +32s net *and* 11s less
  live play cut — better on both axes from one parameter, with 0.5s scoring
  identically, so it is a plateau and not a cliff edge.
- **`min_samples` does nothing.** 4, 6 and 9 score identically: at 3 pose
  samples/s a ±3s window always clears the bar, so the guard that protects the
  ball anchor from a sparse track never binds here.
- **`pad`, `half_window`, `min_fraction` and the activity threshold were left
  alone.** `pad` 2.0 → 1.0 buys +9s of net for 8s more live play — a trade, not an
  improvement, and the wrong direction for this repo. The other three are spiky
  rather than flat under sweep (the threshold runs +482 / +544 / +632 / +595 /
  +613 across 0.65-1.00), which is the shape of noise being fitted on two
  fixtures, not a signal. `v6` is what that looks like when you ship it.

test3 abstains under every rung: the abstain check runs on the ball track before
any pose window is built, so pose cannot reach it. Letting pose override the
abstain is a separate question and deliberately not part of this card.

### Cost

Measured on a Modal T4 over 300s of 1080p30 footage, including the download and
a cold start: **0.26x realtime**, or ~15.6 min of GPU per 60-minute game. Locally
an M4 GPU runs the same pass at 0.48-0.77x realtime.

That is per *whole video*, and it is the reason `condense_use_pose` defaults off.
Stage 3 only ever runs pose inside the windows that survived the highlight gate;
this pass has to see the rallies the gate dropped, so its cost tracks footage
length rather than surviving rallies. `condense_pose_sample_fps` (default 3.0,
matching the ball track's rate) is the lever — every sample is a forward pass.

### What ships

`condense_use_pose = False`. With it on, the anchor is armed
(`condense_pose_anchor_activity = 0.80`,
`condense_pose_anchor_min_seconds = 1.0`) and the gate is not
(`condense_pose_gate_activity = None`) — the gate is kept as a knob so the next
labeled fixture can re-test it, not because it is ready. The pipeline reaches
GPU pose through `extract_keypoints_remote`; the worker image has no torch
(CF-164), so a local run degrades to ball-only windows rather than failing.

## Files
```
metrics.py             pure signal math, both modes (unit-tested in ml/tests/)
harness.py             fixture load, model-clip acquisition, report, results append
diagnose_detection.py  why a rally was missed: BLIND / SPARSE / GATED breakdown
tune_contacts.py       sweep find_contacts tunables over a dumped ball track
deadtime_variants.py   the builder ladder: v0 = mode=rules, v5 = mode=guarded (CF-187),
                       v6-v8 = the pose rungs (CF-198)
visualize_deadtime.py  score every variant on every fixture -> HTML (CF-187)
build_pose_cache.py    cache a full-video keypoint pass per fixture (CF-198)
pose_caches/           {source_video_md5}.json keypoint passes — gitignored
fixtures/              one JSON per test case (ground truth)
                       {test_id}.json          highlights (CF-55)
                       {test_id}_deadtime.json ball-in-play spans (CF-98)
                       README_deadtime.md      dead-time fixture format
ball_caches/           gitignored: {md5}.json ball tracks, for visualize_deadtime.py
results/               {test_id}.jsonl and {test_id}_deadtime.jsonl —
                       one row per tagged run, committed
                       deadtime_visualization.html — generated, gitignored
```
