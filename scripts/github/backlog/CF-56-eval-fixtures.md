<!-- title: CF-56 · Ground-truth fixtures: labeling protocol + Test1 + bootstrap -->
<!-- labels: eval, P1 -->
The human-labeled fixtures the harness (CF-55) consumes.

**Labeling protocol (this is what makes the numbers mean something):** (1) label from the **raw video, never from the app's clips** — labeling off model output makes coverage circular. (2) Mark **tight action spans**, not padded boundaries — the harness measures slop drift vs the design pads. (3) One labeler per fixture, named in the file.

**Fixture format** `ml/eval/fixtures/{test_id}.json`: `{test_id, source_video_md5 (same identity as the ball-cache — pin by content MD5, not game_id), source_r2_key, video_duration_sec, labeler, labeled_at, labeling, clips:[{start,end,note}]}`. start/end accept mm:ss / hh:mm:ss / seconds.

**Deliverables:** (1) Test1 — label the agreed ~1h video per protocol. (2) Bootstrap test2/test3 from the 2026-07-05 review notes (34 playoffs + 35 isolated clips) marked `"labeling": "model-anchored"` (coverage optimistic, but slop/junk/buckets/AUC valid) — makes the harness useful day one. (3) "how to label" section in `ml/eval/README.md`.

**Acceptance:** Test1 parses, MD5 matches an R2 object, clean harness run; bootstrap fixtures committed with caveat.

**Depends:** fixture format agreed with CF-55. Labeling has no code dep — start now.
