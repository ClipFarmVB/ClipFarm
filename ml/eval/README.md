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
# Offline: replay detection + scoring from the R2 ball-cache. A cache miss
# re-tracks on Modal when it is configured — every video, after a
# TRACKING_CACHE_VERSION bump. On a branch that bumps the version, before its
# `modal deploy`, add `-e MODAL_TOKEN_ID= -e MODAL_TOKEN_SECRET=` so a miss fails
# instead of caching the deployed (old) code's track under the new key.
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

> **A recorded row describes the tree it was run against, not the current one,
> and the `git_commit` field is how you tell.** The CF-174 rows tagged
> `cf174-review-fixes` were recorded at `29a83a5`; their `config_snapshot`
> still lists `MIN_SPEED_PXPS: 120.0`, a constant that branch later deleted, so
> the rows are visibly older than the head that ships them. Nothing has since
> changed the shipping path *for those fixtures* — all three are ≤1080p, the
> later commits move only the >1440p clamp, logging, comments and tests, and
> `ball_contact_scale_enabled` defaults on — but that is an **argument that they
> would reproduce, not a re-run**. The local ball caches
> (`ml/eval/ball_caches/{source_video_md5}.json`) are enough to re-score the
> condense builders without R2 — the `CF-391-*` rows were recorded that way, via
> `visualize_deadtime --dump-windows` and `--windows-json` — though re-running
> `--offline` itself still needs the video. Read a row against its own
> `git_commit` before comparing it to anything.

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

The trap worth stating here, because the harness does not error when you hit it
(the fixture tests catch it when the fixture is committed, unless it is a copied
clip list with no highlight sibling to compare against, or with its clip times
edited): `keep_tiers`
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

`guarded` became the default on the fixture numbers measured at `42b582f`, not
on a clean sweep. Those numbers are the **switch-off** column below: they
reproduce exactly with the CF-174 switch (`ball_contact_scale_enabled`) off, and
not with it on. So they describe production's current stop-gap
(`BALL_CONTACT_SCALE_ENABLED=false`) and not the code default, which is on.
Which builder looks better on live play depends on which side of that switch you
read, so read the column that matches the deployment first.

> **Measured at `13180a3` (CF-391)** with
> `python -m ml.eval.visualize_deadtime --contact-scale {off,on,soft}`, from the
> five local ball caches `ml/eval/ball_caches/{source_video_md5}.json` (CF-390).
> Each position was run twice and the two runs' dumped windows and HTML were
> byte-identical. The control: switch off, `v0` (`mode=rules`) and `v5`
> (`mode=guarded`) reproduce every figure published at `42b582f` exactly, so the
> other two columns differ because the switch moved, not the environment.
> `soft` is the switch on plus CF-376's `ball_contact_soft_scale_enabled`.
> `13180a3` is a commit on the CF-391 branch, which `main` squash-merged, so
> it does not resolve from `main`; the figures were measured there and the
> merge left them unchanged, since nothing on the branch after it touched the
> ladder's builders, scoring or inputs.

Each cell is `rules` → `guarded`, dead time removed then live play cut.

| fixture | switch off (production today) | switch on (code default) | switch on + soft |
|---|---|---|---|
| test1*† | 56.2% → **58.4%** · 176s → **126s** | 56.2% → **58.4%** · 176s → **126s** | 56.2% → **58.4%** · 176s → **126s** |
| test2 | 9.5% → **50.8%** · 2s → 12s | 54.4% → **54.9%** · 31s → **14s** | 40.6% → **53.4%** · 6s → 13s |
| test3*† | 76.5% → 0.0% · 118s → **0s** | 92.8% → 0.0% · 178s → **0s** | 86.5% → 0.0% · 144s → **0s** |
| test4 | 44.2% → **73.3%** · 83s → 100s | 62.9% → **81.0%** · 89s → 115s | 49.5% → **74.7%** · 84s → 102s |
| test5* | 9.6% → **49.8%** · 0s → 20s | 50.4% → **57.2%** · 76s → **51s** | 30.5% → **50.2%** · 14s → 28s |
| live cut, comparable three | 85s → 132s (**+47s**) | 196s → 180s (**−16s**) | 104s → 143s (**+39s**) |
| net, comparable three | +134s → **+533s** | +201s → **+458s** | +315s → **+517s** |
| net, all five | +633s → +1440s | +527s → +1366s | +752s → +1424s |

`* = held out while the variants were tuned.`
`† = excluded from the headline net` — see `EXCLUDED_FROM_TOTALS` in
`visualize_deadtime.py`. test1 is a different labeler on 360p-space footage and
test3 is a game the ball tracker cannot follow, so both measure something other
than which builder is better. test1 is also 360p, where the CF-174 scale is
exactly 1.0, which is why its row is the same in all three columns.

The `42b582f` run read the R2 caches
(`ball-cache/{md5}-volleyball-ball-tracking-0eo7r-3-s{N}.json`, `s10` except
test4's `s20`). Those keys predate CF-231. The key now ends in `-v{N}`
(`ball.TRACKING_CACHE_VERSION`), so `_track_ball_cached` no longer finds any of
them: a run that reaches it on those fixtures misses the cache, and re-tracks on
Modal when it is configured or raises when it is not, until caches exist under
the new key. The ladder above does not reach it — `visualize_deadtime` reads
the local caches only.

The results rows for these runs are in `results/test{n}_deadtime.jsonl`, scored
from the dumped `v5` windows through `--windows-json`: `CF-391-rebaseline`
(switch on), `CF-391-rebaseline-scale-off` and `CF-391-rebaseline-soft`, each
with a `config_snapshot` showing the switch and flag it was run under. `v0` is
the control and has no row.

**Net, at the 4:1 live-cut exchange rate the harness and trainer share.**
`guarded` beats `rules` over the three comparable fixtures at every position:
by 399s with the switch off (+533s against +134s), 257s with it on (+458s
against +201s), and 202s with the soft flag (+517s against +315s). Counting all
five widens every gap, and that larger figure is the misleading one: test1 and
test3 supply most of it, and both are excluded from cross-game comparison.

**Read the live-play column before the net, and read it per position.** The
live-play trade that justified the default holds with the switch off and with
the soft flag, and does not hold with the switch on:

- **Switch off:** `rules` cuts 85s of live play on the comparable three and
  `guarded` cuts 132s, 47s *more*, on each of the three. `guarded`'s net lead
  comes entirely from buying that play back with dead time at 4:1.
- **Switch on:** `rules` cuts 196s and `guarded` 180s, 16s *less*. `guarded`
  cuts less on test2 (31s → 14s) and test5 (76s → 51s) and removes more dead
  time on both, so it is better on both axes there. test4 is the only paid
  trade left (89s → 115s for 18 points of dead time). The cost is on the other
  side: `rules` now removes nearly as much dead time on test2 (54.4% against
  54.9%), so `guarded`'s lead there is the live-play saving and little else.
- **Switch on + soft:** between the two. `guarded` cuts 39s more than `rules`
  (104s → 143s), more on each of the three, for 13-25 points of dead time.

**At switch off — production today — the trade was taken deliberately, and it
is worth knowing it was a call rather than a result.** The dead-time win there
is large and consistent — every fixture it condenses improves, three of them by
29-41 points — and 4:1 is the rate this repo's own harness and trainer score
against. So `guarded` ships on the judgement that the win is worth 47s more
play across those three games. The numbers do not make that decision by
themselves; a different weighting of live play would read them the other way
and reach for `CONDENSE_MODE=rules`. With the switch on there is no such trade
to weigh on the comparable three in total, only on test4.

What should reopen it: a real game where the extra cut lands inside a rally
anyone notices, or any change that widens the trades further (raising the gate or
anchor speeds does exactly that). The known local-vs-global anchor limitation in
`motion_anchor_windows` is the likeliest source of the first.

**On the comparable fixtures `v4` and `v5` score identically at every
position** (+533s, +458s and +517s). Their only difference is the abstain, and
the abstain fires only on test3. The shipping default's one advantage over the
aggressive rung is therefore evidenced by a single excluded fixture — worth
knowing before treating `min_track_rate` as settled. With the switch on, `v3`
(the harm-first rung, which keeps the `rules` pads) nets +459s on the comparable
three, one second ahead of `v5`; whether that should change the default is
#611's question, not this table's.

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

# One side of the CF-174 switch (default: the settings mirror), keeping the
# v0/v5 windows as {test}_{v0|v5}.json for harness --windows-json (CF-391).
python -m ml.eval.visualize_deadtime --contact-scale off --dump-windows DIR
```

`--contact-scale` sets `deadtime_variants.NORMALIZE` / `SOFT_SCALE` for that
run only — `main()` puts them back on return, so a second in-process call starts
from the mirror again; the `tune_contacts` mirror stays pinned to the app defaults.

The HTML is **gitignored** — it is ~1MB of inline SVG that would re-churn its
whole diff on every run. Regenerate it rather than looking for it in git, and
note that doing so needs the ball caches (also gitignored, `ml/eval/ball_caches/`):
a fresh clone cannot rebuild it until those are in place.

Two things to read carefully, because both invert a number's meaning:

- **The padding ceiling** shown per game is the most dead time removable at the
  `rules` pads with *zero* live cut. It bounds `v0`–`v3`. It does **not** bound
  `v4`/`v5`, which shrink the pads to 3/2 with merge 3 — an 8s budget against the
  14s one — so they clear it legitimately rather than by cutting play.
- **A variant can beat the ceiling by cutting real play.** `v4` removes 90.8% of
  test3's dead time against a 23.2% ceiling by cutting 161s of rally with the
  CF-174 switch off, and 95.7% by cutting 190s with it on (CF-391). Read the
  dead-removed column against the live-cut column, never alone.

`TUNED_ON` marks which fixtures the variants were designed against (test2 and
test4); every other column is held out and is starred in the summary. test5 is
the strongest of those — it was labeled after the variants were written, so it
could not have shaped them even indirectly.

## Is the tracker following the ball? (CF-229)

CF-174 normalized the contact px/s thresholds by frame height and could not do
the same for the segmentation ones. `ml/pipeline/ball.py` records why, and the
reason is not a property of those constants: **roughly half of test4's samples
sit at ~0 px/s, because the detector is locked onto something that is not the
ball.** Scaling `SEG_MAX_SPEED_PXPS` merges those stationary detections into
the ball's own segments, whose median then falls under the held/spare-ball
filter, and the fixture collapses to zero contacts.

So both segmentation constants are pinned by one upstream defect, and
[#233](https://github.com/ClipFarmVB/ClipFarm/issues/233)'s acceptance is a
number about it. `track_quality.py` prints that number:

```bash
docker compose --env-file .env.docker run --rm --no-deps eval \
  python -m ml.eval.diagnose_detection --test test4

docker compose --env-file .env.docker run --rm --no-deps eval \
  python -m ml.eval.track_quality test4
```

Leave `--dump` off the first command. Its default,
`ml/eval/results/test4_ball_track.json`, is where `track_quality` reads; a
relative `--dump` path resolves against the eval container's working directory
(`/app/api`) and lands somewhere the second command never looks.

It reports the speed distribution in px/s and in frame-heights/s, the fraction
of samples that are effectively stationary, and what the segmentation ceiling
splits today against what a scaled one would split — so the trade is visible
rather than described.

**It cannot tell you about `MAX_JUMP_PX`.** That threshold picks among the
detections a frame returned, during tracking, and a dump holds only the ones
that won. Answering it needs a re-track. The tool says so in its own output,
because the row above that line looks exactly like an answer.

It is an instrument. It changes no threshold — the fix is #233's, and that card
is assigned.

### The static-site filter (CF-229, ships off)

`ball.drop_static_sites` is #233's first half: it drops detections that dwell at
a fixed object, after the ball cache loads and before contacts, rallies and the
condense stage. It is behind `ball_static_site_filter_enabled` (default
`False`), mirrored for the eval tools as `tune_contacts.STATIC_SITE_FILTER` and
applied where they load a track (`load`, `load_track`, `load_game`), so with it
off every number in this README is unchanged. To measure it on:

```bash
python -m ml.eval.track_quality test4 --static-site-filter on
python -m ml.eval.visualize_deadtime --static-site-filter on   # any --contact-scale
```

`--dump-windows` records the position as `"static_site_filter"`, and
`harness --windows-json` refuses a dump whose position disagrees with
`BALL_STATIC_SITE_FILTER_ENABLED`, as it does for the contact scale. The
measurements and the open trade-offs (held serves, the guarded builder's
abstain margin on test4) are in the PR that added it; the rally counts there
are from unlabelled 1080p fixtures and are evidence for the flag, not for a
default.

## Measuring the MIN_RALLY_CONTACTS cliff (CF-376)

CF-174 scaled the contact **speed** thresholds by `frame_height / 360` and left
`MIN_RALLY_CONTACTS = 3` an absolute count. A rally that drops from 3 detected
contacts to 2 is therefore not shortened — it is discarded whole, and
production saw 34 clips become 11 on the same ball cache.

`contact_cliff.py` prints the population that gate is deleting, off the same
dumped track `tune_contacts` reads, in three columns: the CF-174 switch on, the
switch on with CF-376's soft flag (`find_contacts(soft_scale=True)`, the gate
scale raised to `ball.CONTACT_SOFT_SCALE_EXPONENT`, or to `--exponent`), and the
switch off. A fixture with no dump — test1 — is read from its R2 ball cache
under the fixture's `source_video_md5` instead (`tune_contacts.load_track`):

```bash
# The dump first, if you do not have it already: the tool reads a dumped track
# and never a video, and results/{test_id}_ball_track.json is gitignored, so a
# fresh clone has none. `tune_contacts` needs the same file.
#
# No --dump flag: the default is already harness.RESULTS_DIR, which is where
# tune_contacts.load reads from. A relative path here would resolve against
# the image's WORKDIR (/app/api, from Dockerfile.api; the eval service sets no
# working_dir), so `--dump results/...` writes to /app/api/results/ and the
# next command dies with FileNotFoundError looking in /app/ml/eval/results/.
docker compose --env-file .env.docker run --rm --no-deps eval \
  python -m ml.eval.diagnose_detection --test test2

docker compose --env-file .env.docker run --rm --no-deps eval \
  python -m ml.eval.contact_cliff test2
docker compose --env-file .env.docker run --rm --no-deps eval \
  python -m ml.eval.contact_cliff test2 --exponent 0.75
```

Read it on a **1080p** fixture (test2-test5). On test1 the scale is exactly
1.0, so the three columns are the same run and the report says so — that is the
gap CF-375 (#475) exists to close, not evidence that there is no cliff. The soft
column is identical there by construction too (`1.0 ** e == 1.0`).

The soft flag's whole trade, rally counts and both condense builders' dead-time
numbers per exponent, is `tune_contacts --exponents`:

```bash
docker compose --env-file .env.docker run --rm --no-deps eval \
  python -m ml.eval.tune_contacts test2 --exponents
```

It prints switch off, switch on, and switch on + soft at e ∈ {1, 0.75, 0.5,
0.25}, each scored on the rules path this tool replays and on the guarded one
(`deadtime_variants.v5_guarded`, the builder production ships). Soft at e=1 is
the switch-on row again by construction and is there as the control. The rally
column is a count on unlabelled footage — it says how many rallies the detector
emits, not whether they are real.

The ladder separates two gates: `MIN_RALLY_CONTACTS` deletes segments with too
few contacts, and `MIN_RALLY_DURATION` (2.0s) then deletes what is left if the
clip is too short.

**Expect the duration channel to read zero, and read that as a result rather
than as luck.** A rally spans `min(video_duration, last + POST_PLAY_PAD) −
max(0, first − PRE_RALLY_PAD)`. The clamp and the pre-roll split independently,
giving four cases:

| | span | floor |
|---|---|---|
| unclamped, `first ≤ 2` | `last + 2.5` | 2.5 |
| unclamped, `first > 2` | `(last − first) + 4.5` | 4.5 |
| clamped, `first ≤ 2` | `video_duration` (flat in `first`) | `video_duration` |
| clamped, `first > 2` | `video_duration − first + 2` | **2.0** |

Only the last row approaches the gate: there the span moves one-for-one with the
first contact and bottoms out at exactly 2.0s, for a rally whose first contact
lands on the final frame. So for contacts inside the video the floor is
`MIN_RALLY_DURATION` itself and the gate's `>=` is what keeps that case.

Measured over 300,000 random in-video triples at durations from 2s to 4000s:
minimum span 2.0, no case below the gate. Only a video shorter than 2s trips
it, and the fixtures run 300s to 3660s. So tightening contact detection cannot
trip this gate, and a zero here is not "this fixture happened to miss it".

That is worth having measured: it retires the possibility that some of the loss
belongs to the duration gate, and it is a property of the constants, so it stops
holding the moment a pad or the gate moves. The last line also counts the
rallies sitting at exactly 3 contacts, which is the population one detection
away from the count gate.

It measures and decides nothing. The candidate fix #476 led to is the soft
flag (`BALL_CONTACT_SOFT_SCALE_ENABLED`), which ships off.

## Training the learned in-play classifier (CF-393)

`train_deadtime.py` fits the per-second logistic model in
`ml/pipeline/dead_time_ml.py` on the five dead-time fixtures and writes
`ml/pipeline/deadtime_ml_weights.json`. It reads the same local caches as the
ladder (through `deadtime_variants.load_game`), so copy them into
`ball_caches/` first.

```bash
python -m ml.eval.train_deadtime              # leave-one-game-out report + write the weights
python -m ml.eval.train_deadtime --no-write   # report only
python -m ml.eval.train_deadtime --ablation   # also the epic's feature-subset AUC table
```

Every fixture gets a fold: the model and its threshold come from the other four,
and the held-out row sits beside `guarded` (`v5`) on the same game. Headline
totals are over test2/test4/test5 only, as above. The file is written with
`"validated": false` and nothing reads it yet (the builder is CF-394), so
committing new weights enables nothing. Two runs at the same commit are
byte-identical; `git_commit` records HEAD, so a run at another commit differs
in that field.

## Files
```
metrics.py             pure signal math, both modes (unit-tested in ml/tests/)
harness.py             fixture load, model-clip acquisition, report, results append
diagnose_detection.py  why a rally was missed: BLIND / SPARSE / GATED breakdown
tune_contacts.py       sweep find_contacts tunables over a dumped ball track;
                       --exponents for the CF-376 soft-scale axis
track_quality.py       is the tracker following the ball? the lock-on fraction
                       CF-229's acceptance is written in (#233)
contact_cliff.py       how many rallies MIN_RALLY_CONTACTS deletes under CF-174
                       scaling: switch on, on + soft, off (CF-376)
deadtime_variants.py   the builder ladder: v0 = mode=rules, v5 = mode=guarded (CF-187)
visualize_deadtime.py  score every variant on every fixture -> HTML (CF-187)
train_deadtime.py      leave-one-game-out trainer for the in-play weights (CF-393)
fixtures/              one JSON per test case (ground truth)
                       {test_id}.json          highlights (CF-55)
                       {test_id}_deadtime.json ball-in-play spans (CF-98)
                       README_deadtime.md      dead-time fixture format
ball_caches/           gitignored: {md5}.json ball tracks, for visualize_deadtime.py
                       and train_deadtime.py
results/               {test_id}.jsonl and {test_id}_deadtime.jsonl —
                       one row per tagged run, committed
                       deadtime_visualization.html — generated, gitignored
```
