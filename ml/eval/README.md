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

## Which condense builder runs (CF-187)

`condense_mode` in `app.config` picks the keep-window builder, and every mode
lives in `ml/pipeline/dead_time.py`:

| mode | builder | notes |
|---|---|---|
| `rules` | `active_windows_from_contacts` + `bridge_windows_by_motion` | the CF-46 path; still the fallback when `guarded` raises |
| `guarded` | `active_windows_guarded` | **the default.** Speed-gated contacts, motion anchors, tight pads, and an abstain when the ball track is too sparse |

The guarded path became the default on the fixture numbers below, not on a
clean sweep — it buys dead time with live play on three of the four it
condenses, and that trade is the thing to look at before touching its tunables:

> **Measured at `42b582f`, after the NaN change**, from the five R2 ball caches
> (`ball-cache/{md5}-volleyball-ball-tracking-0eo7r-3-s{N}.json`, `s10` except
> test4's `s20`), two consecutive runs byte-identical. The control is what makes
> them trustworthy: `v0` (`mode=rules`) reproduces its documented figures
> exactly on four of five fixtures, so a `v5` difference is the builder having
> changed, not the environment. The fifth is test5, whose `rules` baseline is
> **9.6%, not the 4.6%** published until now — a doc error independent of this
> card, corrected below.

| fixture | dead removed | live cut | |
|---|---|---|---|
| test1*† | 56.2% → **58.4%** | 176s → **126s** | strictly better |
| test2 | 9.5% → **50.8%** | 2s → 12s | +10s of play for 41 points of dead time |
| test3*† | 76.5% → 0.0% | 118s → **0s** | abstains rather than cut 118s of rally |
| test4 | 44.2% → **73.3%** | 83s → 100s | +17s of play for 29 points of dead time |
| test5* | 9.6% → **49.8%** | 0s → 20s | +20s of play for 40 points of dead time |

`* = held out while the variants were tuned.`
`† = excluded from the headline net` — see `EXCLUDED_FROM_TOTALS` in
`visualize_deadtime.py`. test1 is a different labeler on 360p-space footage and
test3 is a game the ball tracker cannot follow, so both measure something other
than which builder is better.

> **Every figure above is stale on the 1080p fixtures as of CF-174 — both
> columns, not just `rules`.** Two separate reasons, and the second one is easy
> to miss:
>
> - `rules` was measured before `bridge_windows_by_motion` took a `frame_height`,
>   so `v0` bridged 1080p footage at a 360p threshold — 150 px/s where production
>   now runs 450.
> - **Both** builders consume `game.contacts`, and `deadtime_variants.load_game`
>   builds those with `find_contacts(tracker, frame_height=frame_h)`. CF-174
>   scales `CONTACT_HIT_SPEED_PXPS` and `CONTACT_RESIDUAL_MIN_PXPS` inside that
>   call — 240 → 720 on the 1080p fixtures — so `guarded`'s windows move too,
>   even though it never touches the bridge.
>
> test1 is the exception on both counts: it is 360p, so the scale is exactly 1.0.
> Everything else — test2, test3, test4, test5, in either column — is a
> pre-CF-174 number, and the **+533s vs +134s net below is computed from two
> stale columns**, so read it as the last measured comparison rather than the
> current one. Re-running the ladder moves the `rules` figures toward *more* dead
> time removed and more live play cut, because fewer gaps clear the higher bar;
> the direction on `guarded` is not predictable from the threshold alone.
> Regenerating needs the R2 ball caches, so these figures stand as the last
> measured ones until someone has them.

Strictly better on one, a paid trade on three, an abstain on one. At the 4:1
live-cut exchange rate the harness and trainer share, that nets **+533s against
`rules`' +134s** over the three comparable fixtures. Counting all five gives
+1440s against +633s, and that larger figure is the misleading one: test1 and
test3 supply most of the gap, and both are excluded from cross-game comparison.

**Read the live-play column before the net.** On the three comparable fixtures
`rules` cuts 85s of live play and `guarded` cuts 132s — 47s *more*, on the axis
this repo protects, on every one of them. The net still favours `guarded` by a
wide margin, but it does so entirely by buying that play back with dead time at
4:1.

**That trade was taken deliberately, and it is worth knowing it was a call
rather than a result.** The dead-time win is large and consistent — every fixture
it condenses improves, three of them by 29-41 points — and 4:1 is the rate this
repo's own harness and trainer score against. So `guarded` ships as the default
on the judgement that the win is worth 47s more play across those three games.
The numbers above do not make that decision by themselves; a different weighting
of live play would read them the other way and reach for `CONDENSE_MODE=rules`.

What should reopen it: a real game where the extra cut lands inside a rally
anyone notices, or any change that widens the trades further (raising the gate or
anchor speeds does exactly that). The known local-vs-global anchor limitation in
`motion_anchor_windows` is the likeliest source of the first.

**On the comparable fixtures `v4` and `v5` score identically (+533s each).**
Their only difference is the abstain, and the abstain fires only on test3. The
shipping default's one advantage over the aggressive rung is therefore evidenced
by a single excluded fixture — worth knowing before treating `min_track_rate` as
settled.

**Abstaining is a real outcome, not a failure.** Below
`condense_guard_min_track_rate` usable speed samples/s the builder returns one
whole-video window; the condense stage sees that nothing meaningful would be
trimmed and ships the game with no condensed cut at all. A 0.0% dead-removed row
in the harness means this, and the offline runner prints `ABSTAINED` so it can't
be mistaken for a broken run.

**The abstain threshold is unconstrained, not tuned.** Only test3 is below it
(0.57 usable speed samples/s — its fixture note records 0.76, which is the *raw*
track rate, a different measure); the other four sit at 1.51-2.99. The default 1.0
stands in that gap, but nothing has been measured inside it, and a game landing
between 0.57 and 1.51 flips between condensing normally and shipping no condensed
video — the most user-visible outcome on this path. A fixture in the gap is what
would settle it; until one exists, treat a move of
`condense_guard_min_track_rate` as unevidenced in either direction.

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

## Files
```
metrics.py             pure signal math, both modes (unit-tested in ml/tests/)
harness.py             fixture load, model-clip acquisition, report, results append
diagnose_detection.py  why a rally was missed: BLIND / SPARSE / GATED breakdown
tune_contacts.py       sweep find_contacts tunables over a dumped ball track
deadtime_variants.py   the builder ladder: v0 = mode=rules, v5 = mode=guarded (CF-187)
visualize_deadtime.py  score every variant on every fixture -> HTML (CF-187)
fixtures/              one JSON per test case (ground truth)
                       {test_id}.json          highlights (CF-55)
                       {test_id}_deadtime.json ball-in-play spans (CF-98)
                       README_deadtime.md      dead-time fixture format
ball_caches/           gitignored: {md5}.json ball tracks, for visualize_deadtime.py
results/               {test_id}.jsonl and {test_id}_deadtime.jsonl —
                       one row per tagged run, committed
                       deadtime_visualization.html — generated, gitignored
```
