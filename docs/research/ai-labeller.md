# An AI labeller for in-play spans (CF-594)

**Status: tooling built; validation pending.** No fixture video was available
on the machine this was built on (`CLIPFARM_FIXTURE_VIDEO_DIR` unset, no
`.mp4` anywhere on disk), so the blind run against test2–5 has not happened,
no agreement number exists, and **no draft fixture has been produced**. This
page records the protocol, the bar a validation run has to clear, and the
exact commands, so that the run is one sitting once the videos are to hand.
The ticket's fallback demo on one local unlabelled game from
`CLIPFARM_EXTRA_VIDEO_DIR` is pending for the same reason; the tooling has
been exercised on a synthetic ffmpeg clip only. It makes no claim about the
labeller's accuracy.

## What it is

A repeatable way to label a game's in-play spans (serve contact → point end,
the dead-time fixture's unit) without a person watching the game:

1. **ffmpeg** pulls one frame per second off the raw video into contact
   sheets (6×5 tiles, 30 s per sheet), plus denser frames (2 fps, ±4 s)
   around any transition the sheet pass could not place.
2. **Claude reads the sheets in the session** (vision; it costs usage, not
   money — no API call, no GPU) and writes down a span list with a
   confidence per span.
3. **The draft writer** (`ml/eval/ai_label.py write`) validates the list and
   emits a fixture in the dead-time format, stamped `labeler: claude-vision`,
   `labeling: ai-draft`, with every span carrying `confidence` and `review`.

The output is a *draft*. It becomes ground truth only after a person reviews
it, and the tooling is built so a draft cannot be mistaken for a human pass:
the stamps are constants, not parameters; the file lands in
`ml/eval/fixtures/drafts/`, which the fixture-pinning suite does not glob;
the writer refuses to write into `fixtures/` or over any file that is not
already an AI draft; and `ml/tests/test_ai_label.py` fails if an
`ai-draft` file ever sits at fixture level.

## The protocol

Follow `ml/eval/fixtures/README_deadtime.md` for what a span *is*; this is
how to read one off frames.

**Coarse pass — contact sheets at 1 fps.** `sheets` writes
`sheet_NNN.jpg` into `ml/eval/frames/<video>/sheets/` with `sheets.json`
beside them mapping every tile to its second. Read the sheets in order. A
rally is a run of tiles with the ball in the air and players moving on both
sides of the net; dead time is players walking, retrieving the ball, the
server waiting, huddles, breaks. Mark the serve contact as the first tile
after the serving hand has struck the ball (the toss and the pre-serve
ritual are *before* the span — the fixtures are tight; the condense pads add
the ritual back) and the point end as the tile where the ball is dead (on
the floor, out, in the net, the whistle). At 1 fps a boundary read this way
is late by 0 to 1 s — the tile marked is the first after the event — never
early.

**Dense pass — where the sheet was unsure.** For a boundary the sheet pass
cannot place within a tile — the ball is a blur, the serve is off-screen,
two rallies run together across a quick replay — pull `frames --around T`
(2 fps, ±4 s, by default) and place it off those. Do not do this for every
boundary: 30 rallies is 60 dense pulls of 16 frames each, and the sheet pass
already places most boundaries to the second. The dense pass is for lowering
`review` flags, not for sub-second precision the human labels do not have
either (every human fixture is labelled to the second).

**Confidence.** Record how sure the labeller is that the span is one rally
with both boundaries placed: 0.9+ when both ends are clear on the sheet or
after a dense pass; around 0.7 when one end was inferred (e.g. the point end
is between tiles but the next tile is unambiguous dead time); below 0.7 when
the labeller is guessing at either the existence of the rally or where it
ends. **Below `REVIEW_BELOW = 0.7` the writer forces `review: true`** whatever
the caller passed, and counts it in `review_count`. The threshold is a
constant in the module, not a setting, so the flag means the same thing in
every draft a reviewer opens. 0.7 is a first placement: it is the point on
the scale above where the labeller has seen both boundaries. The validation
run is where it gets checked — if boundary error does not step up below it,
move it; if flagged spans are no worse than unflagged ones, the confidence
scale is not carrying information and the rule needs a different signal.

**Flag, don't guess.** A span the labeller cannot place goes in with low
confidence and a note saying what was unclear ("serve not visible; start is
first receive tile"). Leaving it out silently is the one failure the reviewer
cannot see.

**Write before scoring.** The span list goes into a JSON file and through
`write`; only then does `score` run. Nothing in the protocol opens the human
fixture: `locate` reads a fixture file for its `source_video_md5` and prints
a path, nothing else. If the session that labels a validation game has read
`test2_deadtime.json` at any point before `write`, the run is not blind and
its number does not count — say so in the report rather than quoting it.

## The agreement bar (proposed)

CF-593's matcher (`ml/eval/rally_match.py`, not yet landed) is what turns a
draft and a human fixture into rally recall, rally precision and per-boundary
error. Until it lands, `ai_label score` reports seconds-level agreement via
`metrics.evaluate_deadtime`, the same numbers the condense harness prints,
with the draft standing in as the model's keep-windows.

Proposed bar, to be met on each of test2–5 separately, not on their average:

| Signal | Bar | Why there |
|---|---|---|
| Rally recall (human rallies the draft has a match for) | ≥ 0.90 | A rally missing from an answer key *inverts* the metric for it: a model that correctly keeps it scores as missed dead time (`README_deadtime.md`, "the trap"). At ~30 rallies per 10-minute game, 0.90 is ≤ 3 such inversions, which a spot-check of the flagged spans can plausibly catch; below it the reviewer is re-labelling, not spot-checking. |
| Rally precision (draft spans that match a human rally) | ≥ 0.90 | The mirror failure: dead time labelled as play is scored as the model over-cutting live ball — the harm signal, the one that matters. Same arithmetic. |
| Boundary error, median | ≤ 1 s | 1 fps sampling puts a boundary 0 to 1 s late (the first tile after the event); a median past 1 s means transitions are being misread, not sampled coarsely. |
| Boundary error, p90 | ≤ 2 s | Inside the smallest pad the condense stage applies (`active_windows_guarded` pads 3.0 s before / 2.0 s after; `rules` 5/4), so a draft boundary is at worst as far from the serve as the model is *designed* to be. The human fixtures carry labeler-to-labeler boundary drift of their own — `visualize_deadtime.py` excludes test1 from the headline net partly because a different labeler's boundary style scores as false over-cut — and that drift is not quantified anywhere, which is the other reason to ask for the matcher's per-boundary numbers rather than a seconds total. |
| Seconds-level, until the matcher lands: `Live wrongly removed` (human play the draft lacks) | ≤ 5 % of human play | The proxy for recall that `score` can report today. |
| `Dead-time removed` (human dead time the draft also calls dead) | ≥ 95 % | The proxy for precision. |

If the bar is met on all four, the labeller is good enough to produce drafts
for a person to spot-check (the ticket's "only if agreement is good" step),
with the review flags as the spot-check list. If it is met on three and
missed on one, say which and why before producing anything — test3 is the
gym whose ball the tracker cannot follow and test4's human labels run 7 s
past the video end, and either can produce a number that is about the
fixture rather than the labeller. If it is missed on two or more, the
labeller does not replace the person, and the write-up should say what it
got wrong (boundaries vs. existence of rallies) so the protocol can change.

Two things the bar does not cover and the run should note anyway: how many
spans were flagged (a draft that flags half its spans has not shrunk the
person's job), and whether the flagged spans were in fact the wrong ones.

## Running the validation

Set `CLIPFARM_FIXTURE_VIDEO_DIR` to the directory holding the test2–5 source
files. They are matched by content MD5 against each fixture's
`source_video_md5`, so the names do not matter and a re-encoded copy will
correctly fail to match. (These two variables are introduced by this
tooling; nothing else in the repo reads them.)

```bash
export CLIPFARM_FIXTURE_VIDEO_DIR=/path/to/fixture/videos

# 1. find the file for the fixture (reads only its md5; prints a path)
V=$(python -m ml.eval.ai_label locate --test test2)
python -m ml.eval.ai_label probe "$V"          # duration / size / fps, for the record

# 2. contact sheets at 1 fps, 6x5 (30 s per sheet) -> ml/eval/frames/<video>/sheets/
python -m ml.eval.ai_label sheets "$V"

# 3. read the sheets in the session; for an unclear boundary around T seconds:
python -m ml.eval.ai_label frames "$V" --around 754      # 2 fps, +-4 s -> .../dense_754s/

# 4. write the span list (mm:ss or seconds; confidence per span; note what was unclear)
cat > /tmp/test2_spans.json <<'JSON'
[
  {"start": "0:00", "end": "0:10", "confidence": 0.9, "note": "rally in progress at open"},
  {"start": "0:31", "end": "0:38", "confidence": 0.6, "note": "serve off-screen; start is first receive tile"}
]
JSON
python -m ml.eval.ai_label write --test test2 --video "$V" --spans /tmp/test2_spans.json \
    --model claude-fable-5-1
#   -> ml/eval/fixtures/drafts/test2_deadtime.json  (ai-draft; never fixtures/)

# 5. only now: seconds-level agreement with the human fixture
python -m ml.eval.ai_label score --test test2 --audit-limit 0

# 6. once CF-593 lands — rally recall / precision / boundary error:
python -m ml.eval.rally_match --test test2 --draft ml/eval/fixtures/drafts/test2_deadtime.json
```

Repeat for test3, test4, test5, then fill in the table below. The drafts for
test2–5 are the validation artefacts: commit them under `fixtures/drafts/`
with the numbers, so the run is reproducible and the next change to the
protocol has a baseline.

### Results

| Fixture | Human spans | Draft spans | Flagged | Live wrongly removed | Dead-time removed | Rally recall | Rally precision | Boundary median / p90 |
|---|---|---|---|---|---|---|---|---|
| test2 | 27 | — | — | — | — | — | — | — |
| test3 | 32 | — | — | — | — | — | — | — |
| test4 | 46 | — | — | — | — | — | — | — |
| test5 | 29 | — | — | — | — | — | — | — |

*(134 human spans in total. Pending: no fixture videos on this machine.)*

## Labelling an unlabelled game (after validation passes)

Same commands with `CLIPFARM_EXTRA_VIDEO_DIR` holding the local games and a
new `--test` id for each (`game7`, or the venue and date — anything that is
not an existing fixture id). `write` pins the draft to the file by MD5,
duration and frame height as the human fixtures are — the duration rounded
up to the tenth of a second the spans are stored at, so a rally still running
when the video ends fits inside it and the pinning suite's over-run check
(`test_labels_fit_inside_the_declared_duration`) passes a promoted draft.
A reviewed draft is promoted by moving it up one directory, changing `labeler` and
`labeling` to the reviewer's, and adding `source_r2_key` once the file is
uploaded. Until then the draft has no `source_r2_key`, so
`harness --mode deadtime --offline` refuses it, by design. Promotion is for
a new id only: a validation draft (`test2`–`test5`) already has a human
fixture one level up, and moving the draft there would replace the ground
truth with it — those drafts stay in `drafts/` as the run's artefacts.

**Nothing has been labelled yet.** Producing drafts for Nelson to spot-check
waits on the validation run clearing the bar above.

## Decisions recorded here rather than in the code

- **JPEG, not PNG**, for frames and sheets (`-q:v 3`): a 6×5 sheet of 180 px
  tiles is ~65 KB as JPEG and over 1 MB as PNG, and the session reads dozens
  of them per game. Nothing downstream needs lossless.
- **Timestamps live in the sidecar, not the image.** `sheets.json` /
  `frames.json` map every tile and file to its second from the fps filter's
  arithmetic (frame k sits at start + k·N) and list exactly the frames ffmpeg
  emitted — `sheets` pulls the frames in one pass and tiles the sequence in a
  second, rather than tiling the video in one call, because the fps filter
  does not emit the tail tick of a fractional-length video and a listing
  derived from the duration named one tile more than the last sheet held.
  The burnt-in `drawtext` label is a reading aid and is skipped (recorded as
  `burnt_in_timestamps: false`) when ffmpeg lacks the filter or a font. A
  span's time never depends on reading digits off a tile.
- **Tier-less drafts.** test2–5 are tier-less (every span in-play), and the
  loader treats an untagged span as in-play, so a draft lists rallies only.
  Breaks and camera outliers are simply unlabelled gaps.
- **Scoring records nothing.** `score` prints the harness's report and writes
  no results row: a draft is not a model run, and a row for it would sit in
  the results log looking like one.
