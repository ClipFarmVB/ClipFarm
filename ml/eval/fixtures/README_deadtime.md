# Dead-time fixture format (CF-98)

A dead-time fixture is the **answer key** for the condense harness: for one fixed
video, the time spans where a **rally is actually happening** (ball in play).
Everything else is dead time *by definition*, so you only label the in-play
spans — the harness derives dead time as the complement.

## File

`fixtures/{test_id}_deadtime.json` (sits alongside the highlight fixture
`{test_id}.json`; a `_deadtime` fixture is a **separate label pass**, because
"is the ball in play" is a different question than "is this highlight-worthy").

```json
{
  "test_id": "test1",
  "labeler": "Your Name",
  "labeled_at": "2026-07-25",
  "source_video_md5": "<content MD5 — matches the ball-cache key>",
  "source_r2_key": "raw/<game_id>.mp4",
  "video_duration_sec": 3660.0,
  "keep_tiers": ["M", "C", "N"],
  "spans": [
    { "start": "01:30", "end": "02:01", "tier": "C", "note": "rally" },
    { "start": "03:24", "end": "03:27", "tier": "N", "note": "failed serve into net" },
    { "start": "16:05", "end": "19:00", "tier": "B", "note": "BREAK" }
  ]
}
```

- `spans` — every labeled span. `start`/`end` accept `mm:ss`, `hh:mm:ss`, or raw
  seconds. (`keep` is accepted as the older key name, for fixtures that list
  in-play spans only and carry no tiers.)
- `keep_tiers` — which tiers count as **ball-in-play**. Everything else falls
  through to dead time via the complement. Omit it, or omit a span's `tier`, and
  the span counts as in-play — permissive beats silently dropping labeled data.
- `video_duration_sec` — the full video length. **Required for correct numbers:**
  dead time is `[0, duration]` minus the in-play spans, so without it the
  dead-time total is wrong (it falls back to the last rally end and undercounts
  trailing dead time).
- Pin the video by **content MD5**, not a game id — game rows get deleted; the
  hash identifies the exact bytes forever and matches the ball-cache key.

## How to label (the protocol)

- Mark a rally from the **first contact / serve** to the **point ending** (ball
  dead, whistle). Tight boundaries — the harness measures over-cut against these.
- Label from the **raw video**, not the app's condensed output (labeling off the
  model's cuts would make the score circular).
- One labeler per fixture, named in the file.
- You can reuse the **same source video** as the highlight fixture — just label
  *continuous play* rather than *highlight-worthiness*.

### The trap: a boring rally is still in play

**Do not reuse a highlight fixture's clip list as the in-play set.** The two
label passes answer different questions, and the highlight fixture
(`test1.json`, tiers `M`/`C`) deliberately omits every rally that isn't worth
clipping — failed serves, shanked receives, "average play".

Those are still **live ball**, and the condense stage is built to keep them. If
they land in dead time, the metrics invert: a model that correctly keeps a
boring rally is scored as *missing dead time*, and one that aggressively cuts
real play scores as *removing more dead time*. You would be rewarding the exact
failure this harness exists to catch.

So, for `test1`:

| Tier | Meaning | In play? |
| ---- | ------- | -------- |
| `M`  | must clip — highlight rally | **yes** |
| `C`  | can clip — highlight rally | **yes** |
| `N`  | no-clip / bad play (failed serve, shank, average rally) | **yes — still live ball** |
| `B`  | break between sets/games | no → dead |
| `O`  | outlier — camera knocked/adjusted, pause in play | no → dead |

Only genuine stoppages (`B`, `O`) are dead. `ml/tests/test_eval_fixtures.py`
enforces this: it asserts every `N` span is kept, and that the `M`/`C` subset
still matches `test1.json` exactly, so the two fixtures can't drift apart.

## What the harness does with it

`dead time = [0, duration] − in-play spans` (the spans your `keep_tiers` selects).
It compares the model's keep-windows to yours and reports two headline numbers
plus two timestamped audit lists:

- **Dead-time removed %** — how much true dead time got cut (aggressiveness).
- **Live wrongly removed** — real play the model cut (the harm; matters more).
- **OVER-CUT LIVE** / **MISSED DEAD** — the divergences, `[start-end]`, longest
  first, with a count and total for each list.

A real run diverges in hundreds of places, so each audit list prints only its
worst 12 spans and summarizes the rest — the lists are sorted longest-first, so
the long spans hold most of the seconds. Pass `--audit-limit 0` for the full
lists, or read them from the results log, which always stores every span.

## Run it

```bash
# laptop-friendly: score a dumped keep-window list against the fixture
python -m ml.eval.harness --mode deadtime --test test1 --version my-change \
  --windows-json keep_dump.json

# in the eval container: derive keep-windows from the real video (R2 ball-cache)
# (`docker compose --env-file .env.docker run --rm --no-deps eval ...` — not `worker`, which carries
#  production's resource limits since CF-241)
python -m ml.eval.harness --mode deadtime --test test1 --version my-change --offline
```

The fast loop: pass `--dump-windows ml/eval/results/test1_keep.json` to an
`--offline` run once, and every later run can re-score those windows anywhere
via `--windows-json` — no Docker, no video download. A fresh `--offline` run is
only needed when the condense logic or its `condense_*` settings change (the
dump records what the model *did*, so metric/fixture/report changes don't
invalidate it). The dump also carries the pre-bridge windows under
`keep_pre_bridge`, which `--windows-json` ignores but keeps the CF-46
comparison reconstructible.

`keep_dump.json` is `{"keep": [{"start": "...", "end": "..."}, ...]}` — the
model's keep-windows; `--windows-json` needs no video, so it runs on a laptop.

`--offline` derives the windows from the fixture's `source_r2_key` video via
`dead_time.py`, exactly as the condense stage does (ball-cache → contacts →
`active_windows_from_contacts` → `bridge_windows_by_motion`). It scores the
shipping post-bridge windows (and records that row), then prints the pre-bridge
windows unrecorded so you can see what CF-46's motion bridging changed. It needs
the worker deps (R2, cv2, app config) and a ball-cache hit for the video.
