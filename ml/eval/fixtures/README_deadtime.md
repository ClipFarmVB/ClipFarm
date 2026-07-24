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
  "keep": [
    { "start": "01:30", "end": "02:01", "note": "rally" },
    { "start": "02:18", "end": "02:28", "note": "rally" }
  ]
}
```

- `keep` — every **rally / ball-in-play** span. `start`/`end` accept `mm:ss`,
  `hh:mm:ss`, or raw seconds.
- `video_duration_sec` — the full video length. **Required for correct numbers:**
  dead time is `[0, duration]` minus `keep`, so without it the dead-time total is
  wrong (it falls back to the last rally end and undercounts trailing dead time).
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

## What the harness does with it

`dead time = [0, duration] − keep`. It compares the model's keep-windows to yours
and reports two headline numbers plus two timestamped audit lists:

- **Dead-time removed %** — how much true dead time got cut (aggressiveness).
- **Live wrongly removed** — real play the model cut (the harm; matters more).
- **OVER-CUT LIVE** / **MISSED DEAD** — every divergence, `[start-end]`, longest first.

## Run it

```bash
# laptop-friendly: score a dumped keep-window list against the fixture
python -m ml.eval.harness --mode deadtime --test test1 --version my-change \
  --windows-json keep_dump.json
```

`keep_dump.json` is `{"keep": [{"start": "...", "end": "..."}, ...]}` — the
model's keep-windows. (The `--offline` mode that derives these from the real
video via `dead_time.py` is a follow-up; `--windows-json` needs no video.)
