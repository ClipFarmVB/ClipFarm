# Highlight detection and dead-time classification: research plan

**Status: plan only. Nothing in it is adopted, and no pipeline code changes with it.**
Written 2026-11-10 against `main` @ `196822d`. It answers one question: *what should we try
next to make highlight detection and dead-time classification better, in what order, and
how would we know it worked?* Every candidate below comes with a hypothesis, an experiment
shaped for the existing `ml/eval` harness, a success metric and an effort estimate. Card
numbers are left as `CF-??` because this PR creates no issues.

How this was produced: three agents mapped the code (`ml/pipeline/*`, `ml/eval/*`,
`ml/modal_*.py`), the committed results (`ml/eval/results/*.jsonl`, the fold record in
`ml/pipeline/deadtime_ml_weights.json`) and the history (full `git log`, every relevant
issue and PR). Five more surveyed outside work, and a separate reviewer then challenged the
draft. **Source caveat:** the session's network refused arxiv.org, huggingface.co,
openaccess.thecvf.com, zenodo.org, modal.com and ai.google.dev. Licences marked *read*
were read from the LICENSE or README file on GitHub. Paper numbers and prices come from
search-engine excerpts and are marked *(excerpt)*. Re-read the primary page before you
quote one in a PR or act on a licence row. That is the same rule
`docs/research/public-datasets-and-models.md` (CF-601) sets for itself.

This doc builds on CF-601's dataset survey rather than repeating it. Where an existing card
already covers an idea, the plan points to that card and adds only what is new.

---

## 1. Summary

**Biggest gains are upstream of the classifiers, and the binding constraint is data.**

- **Highlights.** The one highlight measurement ever recorded (`results/test1.jsonl`,
  2026-07-14) captured 18.9% of labelled highlight time before the gate and 18.5% after
  it, and missed 31 of 41 labelled highlights outright. The loss is in *detection*
  (tracking, contacts, the 3-contact rule), not in the score gate. That row predates the
  contact-floor fix (CF-103) and the resolution scaling (CF-174), so we do not know today's
  number. Two structural problems on top: the gate is really a loudness gate (no audio
  means zero clips), and ranking inside the survivors is weak (README Known Limitations).
- **Dead time.** The shipped `guarded` builder and the unvalidated `learned` builder land
  within about 5 net-seconds of each other on the three comparable games. `learned` fails
  the CF-389 validation bar on live play cut. **The dominant error for both is deleting
  short rallies outright (1–4 s serves, aces, net serves), not trimming edges**, and the
  two builders lose largely *different* rallies.
- **Data.** There are three comparable labelled games (about 41 min, 101 rallies, one
  labeller) and one 360p highlight fixture. The fixture source videos were deleted by the
  R2 retention sweep (CF-595), and the ball caches exist only on one machine (D-0036).
  Every experiment that needs a new *signal* (audio, pixels, pose) is blocked until the
  videos come back.

**Recommended order, in one line each** (detail in §5):

1. Unblock and measure: recover the videos, put the caches where the loop runs, add
   per-rally metrics, re-measure the highlight baseline. Most of this is existing cards.
2. Cheap wins on cached tracks: combine guarded and learned keep-windows, fix the model
   selection leak, and stop the no-audio zero-clip path.
3. Medium bets that add a new signal through an existing seam, each behind a default-off
   flag: tagger-based cheer, per-second frozen image embeddings for dead time, a
   persisted feature log that a learned ranker can train on, and an A/B of a self-hosted
   volleyball ball tracker.
4. Long bets once there are 20+ labelled games: cheap labelling (one click per rally, a
   VLM as one weak vote), a small spotter over ball and pose tracks, and duration-aware
   decoding.

What the plan **does not** recommend: training end-to-end video models on our data (too
little), using a VLM to set rally boundaries (published results say they are weak at
fine-grained timing), lowering `MIN_RALLY_CONTACTS`, more padding, or adding audio as
features to the in-play classifier. Each of those was measured before and lost (§2.4).

---

## 2. Current state and failure modes (with evidence)

### 2.1 Pipeline as it runs today

| Stage | What runs | Key constants | Where |
|---|---|---|---|
| 0 Audio | ffmpeg → 16 kHz mono → RMS envelope (0.5 s window, 0.1 s hop) | — | `ml/pipeline/audio.py` |
| 1 Ball | Roboflow RF-DETR `volleyball-ball-tracking-0eo7r/3` on a Modal T4, about 3 looks/s (`round(fps/3)`), batch 1, `MIN_CONF` 0.40, single-hypothesis nearest-prediction linker | — | `ml/pipeline/ball.py:494` `track_ball`, `ml/modal_app.py` |
| 1 Contacts | Track segmentation on implausible motion, gravity estimated per video, contact = ballistic residual above a speed-scaled floor | residual floor 240 px/s (CF-103); CF-174 height scaling **off in production** since 2026-10-19 | `ball.py:1042` `find_contacts` |
| 1 Rallies | Group contacts on gaps, pad, cap clip length | gap 5 s, pads 2.0 s / 2.5 s, `MIN_RALLY_CONTACTS = 3`, `MIN_RALLY_DURATION = 2`, 30 s cap split on largest gap | `ball.py:326-333`, `:1340` |
| 2 Score + gate | `0.45·cheer + 0.35·shape (+0.20·CLIP)`; CLIP is off and cannot load in the worker image, so the effective score is `0.5625·cheer + 0.4375·shape`; gate 0.50 | cheer = peak RMS in `[last_contact, +4 s]`, normalised to the video's p99 | `score.py:25-27, 99-125`, `audio.py:305-346` |
| 3 Pose | YOLOv8s-pose @ 1280 every 4th frame, on Modal, inside gate survivors only; changes action labels only | — | `detect.py:456`, `ml/modal_pose.py` |
| 4–5 | Audio confidence weighting; ffmpeg cut at the padded times | — | `audio.py:349`, `clip.py:60` |
| 6 Condense (opt-in) | Keep-windows from the **pre-gate** stage-1 signal. `condense_mode` ∈ `rules` / `guarded` (default) / `learned` (off; falls back learned → guarded → rules) | guarded: speed gate 0.25 fh/s, motion anchor 0.30 fh/s, pads 3/2, merge 3, abstain below 1.0 usable samples/s | `dead_time.py:507-606`, `dead_time_ml.py`, `api/app/workers/tasks.py:212-300` |

Compute (repo's own figures): tracking about 16–19 s per video-minute on the T4; pose about
5.6 s per rally; GPU cost "roughly $0.25 per hour of footage" (`api/app/config.py:637`).
Roboflow credits already exceed the free tier (CF-369), and the RF-DETR weights' licence is
unknown (CF-374).

### 2.2 Invariants this plan must not break

From `README.md` *Key Concepts* and the code. Every candidate in §4 is checked against them.

1. **Ball tracking is the primary path; pose only refines.** New signals may confirm,
   extend or veto a ball-derived rally. None of them may become a second, independent
   source of rallies on a multi-court video (audio and pixels both hear and see the
   neighbouring court).
2. **Contacts are ballistic residuals, not angle or speed thresholds.**
3. **One contact set feeds both highlights and condense** (`tasks.py:1294-1299`). A change
   tuned for one path moves the other, so every detection change is scored on both.
4. **Any change to how tracking works needs a `ball.TRACKING_CACHE_VERSION` bump**, which
   re-tracks every video (paid). Post-cache filters such as `drop_static_sites` avoid it.
5. **Condense semantics:** keep-windows; `[]` is a verdict, an exception falls back,
   `Abstained` means "could not judge". Live play is protected at a 4:1 exchange rate
   (`LIVE_CUT_COST`), and abstaining beats cutting play.
6. **360p `test1` is the invariance control:** a resolution-scaling change must leave it
   bit-identical.
7. **Default-off flags, and a person flips defaults.** Research runs on cached tracks and
   uses no paid compute unless a person approves it (overnight RULES; CF-389 decisions).
8. **Reporting never breaks processing** (CLAUDE.md). A new scorer or embedding step falls
   back to today's behaviour on any exception.
9. **Licences:** AGPL-3.0-or-later repo; code, weights and data are licensed separately;
   non-commercial data taints weights trained on it; "unstated" means all rights reserved
   (`docs/licensing.md`).

### 2.3 Failure modes, ranked by evidence

**Highlight path**

| # | Failure | Evidence |
|---|---|---|
| H1 | **Detection misses most highlights.** | 31 of 41 labelled highlights missed; pre-gate ≈ post-gate (`results/test1.jsonl`, CF-97). Never re-measured since the floor change. |
| H2 | **Tracker identity hops.** The single-hypothesis linker always accepts *some* detection. | 23% of consecutive positions jump more than 200 px; on test4 about 56% of samples sit on static objects (CF-229). The static-site filter cuts that to 7.7% but is off by default (PR #616). |
| H3 | **The 3-contact rule is a cliff.** Short rallies, aces and kills off serve-receive have ≤ 2 detected contacts. | With CF-174 scaling on, production clips fell 34 → 11; 51 of 64 lost 1080p rallies died at the count gate (CF-376). Production now runs the scaling off. |
| H4 | **The gate is a loudness gate.** With cheer = 0 the best possible score is 0.4375 < 0.50. | No audio track, a quiet gym or a muted phone gives **zero clips**, and the stage reports success (CF-294). A loud neighbouring court passes. |
| H5 | **Ranking is weak.** Cheer cannot tell a great rally from a loud celebration or a funny error. | README Known Limitations. A 2026 MMSports study found highlight clips are louder on average but the distributions overlap heavily: "loudness alone is insufficient" *(excerpt, [arXiv 2609.17923](https://arxiv.org/abs/2609.17923))*. |
| H6 | **Fixed pads on contacts set clip ends.** Nothing detects the ball landing or the whistle, so a missed last contact truncates the ending; the 30 s split cuts the longest rallies into separately scored pieces. | `ball.py:1257-1258, 1311-1336`. |
| H7 | **No learning loop.** Rally features and gated-out rallies are not persisted, so a learned ranker has nothing to train on. | `tasks.py:1423-1435`; the only engagement field is `Post.like_count`. |

**Dead-time path** (comparable three = test2, test4, test5; numbers from the committed rows)

| Builder | Summed net s (4:1) | Summed live cut s | Status |
|---|---|---|---|
| rules, scaling on | +201 | 196 | fallback |
| guarded, scaling on | +458.4 | 180.1 | **default** |
| guarded, scaling off (production) | +533 | 132 | default |
| learned, held out, scaling on | +463.3 | 210.5 | off; `validated: false` |

| # | Failure | Evidence |
|---|---|---|
| D1 | **Live-play harm is mostly whole short rallies deleted**, not edges trimmed. | Classified from the stored `over_cut_live` spans: guarded on test4 cuts 115 s, of which 60 s are 14 whole rallies (lengths 1–12 s); learned cuts 147 s, 127 s of it 23 whole rallies. Interior cuts are about 0 everywhere. 27 of the 101 comparable rallies last ≤ 3 s. |
| D2 | **Guarded and learned lose different rallies.** | test4: 10 deleted by both, 4 by guarded only, 13 by learned only; test5 3/5/7; test2 3/1/4. Learned is better at edges, worse at whole rallies. |
| D3 | **The learned model fails its bar, and the pass it has is biased.** | CF-395: clause 1 (net) passes by +4.92 s, but the context variant was chosen on the same held-out folds (the other variant fails by −23.6 s); clause 2 fails (210.5 vs 195.1 s); clause 3 fails on test4 (+31.4 vs +30 s). |
| D4 | **test4's track is the bottleneck, not the classifier.** | Held-out AUC 0.728 on test4 against 0.91–0.95 elsewhere; 19 of its 46 rallies produce no contact (CF-187). |
| D5 | **Tiny n, one labeller, no uncertainty.** | The bar's margins are one rally (a 7 s rally cut whole costs 28 net-seconds). 1 s label quantisation over about 200 endpoints is larger than the bar's 15 s slack. No inter-annotator measurement exists. test1 (360p, different labeller) is 54% of the training rows. |
| D6 | **Features see only the ball track.** | 13 ball-track features (`dead_time_ml.py:84-107`). Nothing sees players walking back, a huddle, a ready stance or the referee. |
| D7 | **Seconds-weighted metrics hide the dominant error.** | No per-rally metric on `main` (CF-593's matcher is not merged); a 2 s serve deleted whole costs the same as 2 s trimmed off a 20 s rally. `validate_learned.KNOWN_DECODERS` still lists only `threshold`, so an HMM model cannot be validated. |

### 2.4 Already tried — do not re-propose without new evidence

Condensed from the history notes; each row is a measured result, not an opinion.

| Idea | Result | Where |
|---|---|---|
| More padding to protect live play | Every step past 5/4 costs 4–32 dead seconds per live second saved | CF-103 |
| Lowering or scaling `MIN_RALLY_CONTACTS` | Cannot recover segments that never form, admits junk; cause is upstream contact recall | CF-376 |
| One-knob or multi-knob contact sweeps fitted to 2–3 games | Masking between knobs; < 1% gain for double the knobs | CF-103, PR #182 |
| Learned in-play classifier on 1–2 games | Cut 100% of play on held-out test2 — learned the venue | CF-173, PR #179 |
| Two-state HMM decoding | Lost net (+341 vs +463 s) while cutting less play (148.5 vs 210.5 s) | CF-396 |
| ±context lags | Lower held-out net than none | CF-393 |
| Audio as features for the in-play classifier | 0.738 AUC alone; 0.914 → 0.916 added — redundant | CF-389 epic |
| Pose gate for dead time | Overfits: tuned +68 s, held-out −63 s | CF-198 branch |
| Pose anchor for dead time | Cuts 39% less live play but needs full-video pose, about 15.6 GPU-min per game-hour | CF-198 branch |
| End-to-end video fine-tuning | Ruled out: "112 labelled minutes is far below what fine-tuning needs" | CF-389 epic |
| Lowering detector confidence for non-Mikasa balls | Added positions no better than chance | CF-171 |
| Motion bridge for condense | Net-negative; collapses when the bridge max exceeds the inter-rally gap | CF-187 |

Note the difference between these and what §4 proposes. Audio is rejected as *in-play
features*, not as a *boundary* or *cheer* signal. HMM is rejected with geometric durations
and a net-only objective, not as a class. Video is rejected as *end-to-end training*, not as
*frozen embeddings with a linear probe*. Each §4 item that sits next to a rejected idea
says why it is different.

---

## 3. What the outside world offers, and what it would buy us

One line per family. Detail and full links are in §7.

| Family | Best candidates (licence) | What it buys ClipFarm | Fit at our data scale |
|---|---|---|---|
| **Volleyball ball trackers** | VballNet / `fast-volleyball-tracking-inference` (MIT code **and** ONNX weights, *read*); WASB-SBDT (MIT code, volleyball weights trained on unstated data); TrackNetV3/V4 (MIT) | A self-hosted tracker that can run on every frame instead of 3/s, removing Roboflow credits (CF-369) and the unknown RF-DETR licence (CF-374). VballNet's author reports F1 0.90 at about 150 fps on a CPU, on 886 beach frames — own numbers, tiny and off-domain. | No training needed for an A/B; fine-tuning via BlurBall's MIT training scripts if it misses. |
| **Volleyball pipelines** | VolleyMole (MIT code; weights carry no licence); masouduut94 VideoMAE SERVICE/PLAY/NO-PLAY (GPL-2.0 app repo, weights and data unstated) | Architecture references: fuse a clip-level game-state classifier with the ball track; rank with rules or a VLM. Nothing shippable as weights. | Copy the idea, not the weights. |
| **Precise event spotting** | E2E-Spot (BSD-3), T-DEED (GPL-3.0, won SoccerNet Ball Action Spotting 2024), AdaSpot (MIT); VNL-STES adapts the family to volleyball at 8 games | Serve and rally-end timestamps from pixels, independent of the ball track. | Marginal: every SoccerNet BAS winner pretrained on the 500-game set first. Public checkpoints are trained on NDA, NC or YouTube footage, so none can ship. |
| **Temporal action localisation** | ActionFormer, TriDet (MIT), TadTR, OpenTAD (Apache-2.0) | A learned rally/dead-time segmenter over frozen features. | A light head on frozen features is realistic; boundary precision at 0.5–1 s feature stride may trail ball residuals. |
| **Keypoint-graph spotting** | UMEG-Net (AAAI 2026, few-shot; **no LICENSE file**, so reimplement) | A spotter over the ball and pose tracks we already compute. | Designed for 25–100 clip training sets *(excerpt)*. |
| **Image / video embeddings** | SigLIP 2, Perception Encoder (PE-Core), DINOv2, VideoPrism (Apache-2.0); V-JEPA 2 (MIT code; weights "MIT" per a secondary source) | Per-second visual features: players walking vs ready, huddles, ball retrieval — what the ball track cannot see. | Linear probe on frozen features is realistic on 134 spans. |
| **Avoid for anything that ships** | VideoMAE v1 and official VideoMAE V2 weights (CC BY-NC 4.0); Qwen2.5-VL-3B (research licence); MS-TCN official code (MIT + Commons Clause, AGPL-incompatible); SoccerNet video (NDA); QVHighlights annotations (CC BY-NC-SA) | — | Eval only, or reimplement from the paper. |
| **Audio taggers** | EfficientAT (MIT; mn10 0.471 AudioSet mAP at 0.54 GMACs per 10 s), YAMNet (Apache-2.0), PANNs (MIT code; weights probably CC BY 4.0, unverified), BEATs (MIT) | Separate cheer, whistle, ball-hit, squeak and music, which RMS lumps together. AudioSet already has Whistle, Cheering, Applause, Crowd, Shout, Whack/thwack and Squeak classes. CPU-feasible. | No training to get a first signal. |
| **Whistle detection** | DSP band-pass detectors: 92.5% sensitivity on 12 h of broadcast audio (2011); `GYdevy/volleyball-whistles` (MIT on its card) | Rally start (serve whistle) and end (point whistle) where a referee exists. | **Neighbouring courts' whistles will bleed in**; no source found on separating them. Optional and gated by our own ball track. |
| **VLMs** | Gemini 2.5 Flash-Lite / Flash, Claude Haiku, self-hosted Qwen3-VL-8B (Apache-2.0 repo) | A coarse judge of "is this 10–40 s gap dead?" and an offline labeller. Estimated $0.02–$0.42 per match for 100 ambiguous 20 s gaps; ~$12–53 to label 50 matches (§4.3 L1 arithmetic, prices *(excerpt)*). | Weak at timing: TennisTV notes fast rallies need dense sampling, and Gemini 2.5 Flash as a temporal labeller scored 29.2 vs 90.3 mAP on THUMOS14 in one 2026 paper *(excerpt)*. Use per segment, never for boundaries. |
| **Weak supervision / labelling** | One click per segment ("timestamp supervision": 97% of full supervision on 50Salads *(excerpt)*); boundary-only active learning (B-ACT 2026 *(excerpt)*); Label Studio TimelineLabels (Apache-2.0); FlyingSquid temporal label model (Apache-2.0, unmaintained — reimplement) | The fastest route past three comparable games. | The point of the exercise. |
| **Smoothing / decoding** | Explicit-duration (semi-Markov) Viterbi; hysteresis; MS-TCN-style refinement (reimplement) | Fewer fragments. MS-TCN on 50Salads: frame accuracy 78.2 → 80.7 while segmental F1@10 went 27.0 → 76.3 *(excerpt)* — AUC hides fragmentation. | Duration models are cheap; a learned refinement stage overfits below ~20 games. |
| **Evaluation at tiny n** | Paired per-game deltas (positive on all 5 games ≈ one-sided p 0.03); within-game block bootstrap over rallies; segmental F1@IoU and edit score; Platt calibration over isotonic below ~1000 samples (scikit-learn docs, *read*) | A verdict we can trust with 3–5 games. Cluster bootstrap over games has poor coverage below ~15–20 groups *(excerpt)*. | Free. |

---

## 4. Ranked candidate improvements

Effort: **S** ≤ 2 days, **M** ≤ 1–2 weeks, **L** multi-week. "Cached" means the experiment
runs on the five local ball caches with CPU only; "videos" means it needs CF-595's recovered
fixture videos; "$" means it needs paid compute a person must approve.

The **standard protocol** referenced below, for every dead-time experiment:

- Nested leave-one-game-out: *every* choice (feature subset, context, decoder, threshold,
  pads) is made on the training games inside the fold; the outer game only scores.
- Report on the comparable three, test1 and test3 separately, at the production switch
  position (CF-174 scaling **off**) and at **on**.
- Paired against `guarded` on the same game: per-game Δnet and Δlive cut, plus a
  within-game block bootstrap over (rally + following gap) units.
- Per-rally metrics next to seconds: rally recall at "≥ 50% kept" and "100% kept", whole
  rallies deleted by length bucket (≤ 3 s, 3–8 s, > 8 s), start and end error.
- A sensitivity rerun with every label endpoint jittered ±1 s.

### 4.0 Prerequisites (mostly existing cards — listed so the order is explicit)

| Item | Card | Why it gates the rest |
|---|---|---|
| Recover fixture source videos to a retention-exempt prefix | CF-595 (#641) | Every new-signal experiment needs pixels or audio. |
| Ball caches where the research runs | D-0036, CF-593 (#639) | Every cached experiment. |
| Rally precision / recall / boundary-error matcher at 1080p | CF-593 (#639) | The common per-rally scorer. |
| A 1080p highlight fixture | CF-375 (#475) | The only highlight fixture is 360p; no 1080p highlight change has ever been measured. |
| Two new-venue labelled games | CF-398 (#519) | Turns "3 games" into 5; adds a lock-box test. |

### 4.1 Quick wins

**Q1 · Re-measure the highlight baseline at current `main`** — S, cached (test1 track) or videos

- *Hypothesis:* the 18.9% captured figure is stale; CF-103's floor change (live cut on
  test1 fell 517 → 176 s on the dead-time side) probably moved highlight capture a lot,
  and every highlight decision below is judged against a number from July.
- *Experiment:* `python -m ml.eval.harness --test test1` (highlight mode) with clips from
  the current pipeline at scaling off / on / soft and static-site filter off / on. Record
  rows; add pre-gate and post-gate. If the 1080p fixture (CF-375) exists, do it too.
- *Metric:* Captured %, well / butchered / missed of 41, Score AUC — the harness's four
  signals.
- *Invariant risk:* none; measurement only.

**Q2 · Combine guarded and learned keep-windows** — S, cached

- *Hypothesis:* the two builders delete different rallies (D2), so a keep-window that is
  the union of both, or "learned, but never cut inside a guarded contact or anchor
  window", recovers most whole-rally deletions while keeping most of learned's tighter
  edges.
- *Experiment:* two `Builder`s added to `VARIANTS` in `ml/eval/deadtime_variants.py`
  (`v8` union, `v9` learned-with-guarded-veto), scored with `visualize_deadtime` at all
  switch positions, held-out learned folds only. No production change.
- *Metric:* summed live cut ≤ guarded's on the comparable three **and** whole rallies
  deleted down by ≥ 25% (guarded deletes 26 of 101), with net ≥ guarded − 20 s. The
  union trivially lowers dead time removed; the question is how much.
- *Invariant risk:* low — keep-window semantics unchanged; `Abstained` from guarded must
  win over a learned window (test3).

**Q3 · Remove the selection leak and make the learned trainer report per-rally** — S, cached

- *Hypothesis:* the learned builder's single passing clause is selection-biased (D3), so
  nobody knows whether it beats guarded. A nested selection plus per-rally reporting gives
  an honest answer, and makes every later dead-time card cheaper to judge.
- *Experiment:* in `ml/eval/train_deadtime.py`, move `choose_variant` / `choose_decoder`
  into an inner LOGO loop; weight training games per game rather than per row (test1 is
  54% of rows); add the per-rally metrics from the standard protocol and the paired-Δ
  table to the fold record; update `validate_learned.KNOWN_DECODERS` so the HMM decoder
  can be validated. Re-run the bar.
- *Metric:* the CF-389 bar on unbiased folds; and the new per-rally numbers for guarded vs
  learned as the reference row every later card compares against.
- *Invariant risk:* none in production; changes eval only.

**Q4 · No-audio and unreliable-audio fallback for the gate** — S, existing card CF-294

- *Hypothesis:* renormalising the score over shape (and later visual) terms when audio is
  missing, silent, clipped or mostly music — the same renormalisation `score.py` already
  does when CLIP is absent — removes a silent zero-clip path without changing behaviour on
  normal footage.
- *Experiment:* CF-294 already owns the fix. Add a test1 run with audio muted to show the
  gate no longer drops everything, and a normal run to show it is bit-identical.
- *Metric:* zero-clip rate on muted footage → non-zero; test1 normal-footage rows unchanged.
- *Invariant risk:* low; renormalisation is an existing pattern.

**Q5 · Gap-type-aware rally ending in the highlight path** — S–M, cached

- *Hypothesis:* a track gap has three causes — detector miss (short, mid-air), ball out of
  frame (ends at the top edge), ball dead (ends low, slows, rolls). Ending a rally at the
  last contact + 2.5 s ignores which one happened, which truncates endings (H6). A rule
  that extends the clip to the last *in-play* track point when the gap is a miss or an
  exit, and trims after a low, slowing track, fixes endings without new weights.
  `fast-volleyball-tracking-inference`'s `track_calculator.py` (MIT) is a working example
  of rolling-ball and "ball handed over for the next serve" trimming.
- *Experiment:* a post-contact end-refinement function evaluated by the CF-593 matcher on
  rally end error, plus the highlight harness's "butchered" bucket on test1. Off by default.
- *Metric:* median end error and "butchered" count down; condense metrics unchanged
  (this must not touch the shared contact set — it only moves the highlight window end).
- *Invariant risk:* medium-low. It does not change contacts or rally *membership*, only
  the end time of an already-accepted rally. Not a re-try of CF-38 (edge-trim of sparse
  contacts); it reads the track after the last contact.

### 4.2 Medium bets

**M1 · Tagger-based cheer and an audio reliability flag** — M, videos

- *Hypothesis:* replacing peak RMS with the posteriors of AudioSet's Cheering, Applause,
  Crowd, Shout and Yell classes in the same post-contact window stops whistles, ball
  slams, music drops and squeaks from counting as cheer, and improves ranking (H5). The
  same pass yields a reliability flag (music fraction, clipping, silence) that drives Q4's
  renormalisation.
- *Model:* EfficientAT mn10 (MIT) or YAMNet (Apache-2.0); run on the worker CPU or in the
  existing Modal container. By MAC arithmetic, minutes of CPU per match, no GPU *(estimate,
  not benchmarked)*. Record the weight licence in `docs/licensing.md` before merging.
- *Experiment:* recompute `cheer_score` for test1 (and the CF-375 fixture) with both
  scorers; highlight harness Score AUC and Captured %, pre-gate and post-gate. Keep the
  `cheer_score` field and 0–1 range so `score.py` is untouched.
- *Metric:* Score AUC up on test1 with Captured % not down; a per-tier check (M ranked
  above C). Single-fixture result — read it as a direction, and repeat on CF-375.
- *Why it is not the rejected audio idea:* CF-389 rejected audio as *in-play features*.
  This is the highlight *ranking* signal, which audio already is.
- *Invariant risk:* low. Still court-blind; does not create rallies.

**M2 · Per-second frozen image embeddings as dead-time features** — M, videos, small $

- *Hypothesis:* the learned model's errors are on rallies the ball track cannot see (D4,
  D6). A frozen image encoder sees players in ready stance, walking back, huddling. A
  linear probe on PCA-reduced embeddings, fused with the 13 ball features, should cut
  whole-rally deletions on test4 in particular.
- *Model:* SigLIP 2-B/16 or PE-Core-B/16 (both Apache-2.0) at 1 fps. Cost estimated at
  $0.03–0.05 per match on an L4/T4, dominated by decoding *(estimate)*. A Modal function
  writes `embeddings/{md5}-{model}-1fps.npy` to R2 next to the ball cache, so it runs once
  per video.
- *Experiment:* extend `dead_time_ml.FEATURE_NAMES` behind a new `FEATURE_VERSION`
  (contract check in `check_contract`), PCA to ≤ 16 dims fitted inside each fold;
  `train_deadtime --ablation` with ball-only / embedding-only / both; standard protocol.
  Optionally run the cheaper CF-397 frame-difference features first as a baseline.
- *Metric:* the CF-389 bar under Q3's nested selection; whole-rally deletions on test4
  down; held-out AUC up on every comparable game, not on average.
- *Why it is not the rejected video idea:* no fine-tuning; one logistic layer over frozen
  features. Data need is the same as today's model.
- *Invariant risk:* medium. Pixels see the neighbouring court, so the embedding may only
  *raise* or *lower* in-play probability inside the existing decoder — it cannot open a
  keep-window where the ball track is dense and still. Production remains behind
  `condense_mode=learned` (off).

**M3 · Persist per-rally features and gated-out rallies** — S–M, no $

- *Hypothesis:* a learned ranker (README: "the plan") and every future scorer experiment
  need labelled examples joined to the features the pipeline actually computed. Today
  those are thrown away (H7). Logging them costs nothing and starts the clock.
- *Experiment:* write each pre-gate rally's `features`, `cheer_score`, score and gate
  decision to a JSON blob on the game (or R2 next to the ball cache); expose the user's
  own actions (download, share, delete, relabel) as weak labels. Reporting-never-breaks:
  a failed write logs and continues.
- *Metric:* coverage — share of processed games with a complete feature log.
- *Invariant risk:* none to detection. Needs a privacy review: it is behavioural data.

**M4 · Learned highlight ranker** — M, after M3 and CF-375

- *Hypothesis:* a pairwise ranker over (cheer, rally shape, M1 tags, optional rally-window
  embedding) trained on test1's M/C tiers plus CF-375's labels ranks better than the hand
  weights. Pairs multiply: 35 rallies give about 600 pairs per game.
- *Experiment:* follow the `dead_time_ml` pattern — committed JSON weights, numpy-only
  inference, `validated` flag, a `highlight_scorer` setting that falls back to the
  heuristic. Leave-one-game-out across highlight fixtures; harness Score AUC.
- *Metric:* Score AUC and Captured % at the 0.50 gate on held-out fixtures; must hold on
  both resolutions.
- *Invariant risk:* low for the ranker; **medium if it is allowed to move the gate**,
  because the gate decides which rallies get pose and clips. Ship as ranking-only first.

**M5 · A/B a self-hosted volleyball ball tracker** — M, videos, $ (approved re-track)

- Extends CF-596 (#642) with specifics. *Hypothesis:* VballNet (MIT weights) or WASB run at
  full frame rate gives fewer identity hops (H2), more contacts on short rallies (H3, D1),
  and removes the Roboflow cost and licence unknowns. Running every frame shrinks the
  1 s `MAX_SAMPLE_GAP_SEC` problem threefold.
- *Experiment:* run the candidate offline on the five fixture videos into the same
  `[BallPosition]` contract, store as a separate cache key, and replay `find_contacts`,
  `contacts_to_rallies` and all three condense builders. Score with CF-593 and the
  dead-time harness; `track_quality.py` for hop rate and static share. CF-42's frame-skip
  saving is lost, so measure decode time on the Modal T4 as part of the result.
- *Metric:* rally recall up and static share down on test4; no regression on test1 (360p);
  contacts per rally closer to the human count; $ per footage-hour vs today's ~$0.25.
- *Invariant risk:* high if adopted — new tracker means a `TRACKING_CACHE_VERSION` bump
  and re-tuning the residual floor. Keep it eval-only until it wins on every fixture.

**M6 · Court region filter applied after the cache** — M, needs multi-court footage

- *Hypothesis:* README calls multi-court junk "the top open detection task". A
  user-confirmed court polygon applied to ball positions *after* the cache (like
  `drop_static_sites`) removes neighbouring-court contacts with no re-track. CF-29 was
  closed, but no court-ROI code is on `main`; only a pose x-band filter exists.
- *Experiment:* add the filter behind a flag; measure on a multi-court video once one is
  labelled. Without one, measure only that single-court fixtures are unchanged.
- *Metric:* junk clip seconds on multi-court footage; zero change on single-court fixtures.
- *Invariant risk:* low (post-cache, flag-off); the risk is a wrong polygon dropping play,
  so the default must be "no polygon = no filter".

### 4.3 Long bets

**L1 · Scale labels cheaply: one click per rally, a VLM as one weak vote, boundary review**
— L, videos, $

- *Hypothesis:* data is the binding constraint (D5). Labelling one point inside each rally
  instead of exact boundaries reached 97% of full supervision on 50Salads *(excerpt)*; a
  review page that asks a person only about the least certain predicted boundaries
  (B-ACT-style) makes each labelled minute go further. A VLM (CF-594's labeller, PR #648)
  is one more vote on clip-level "in play?" questions, never a boundary.
- *Cost (estimates from excerpted prices):* labelling 50 matches end to end with Gemini 2.5
  Flash ≈ $12–24 (batch vs standard); with Claude Haiku ≈ $53. Check each provider's terms
  on training with outputs, and user consent, before sending user video anywhere.
- *Experiment:* label 2–3 games both ways (exact and one-click) and measure how close the
  one-click-trained model gets to the exact-trained one under the standard protocol. Have a
  second person exact-label one existing game to measure label noise — that bounds what
  any Δ in this plan can detect.
- *Metric:* inter-annotator boundary error; one-click vs exact held-out gap; labelled games
  per person-hour.
- *Invariant risk:* none (labels); VLM output is never ground truth.

**L2 · A small spotter over ball and pose tracks for serve and rally end** — L, videos, $

- *Hypothesis:* a temporal model over ball keypoints plus pose skeletons (UMEG-Net's idea,
  reimplemented — its repo has no licence) spots serves and rally ends better than contacts
  + pads, and catches short rallies whose only event is the serve.
- *Cost note:* pose today runs only inside gated windows; full-video pose was measured at
  about 15.6 GPU-min per game-hour (CF-198) and was scoped out for that reason. Run pose at
  low resolution on candidate windows plus the gaps that the dead-time model is unsure about,
  not the whole video.
- *Experiment:* train on the fixtures' rally boundaries; CF-593 start/end error vs
  `contacts_to_rallies`; as a dead-time feature, the standard protocol.
- *Metric:* serve-start error and short-rally recall.
- *Invariant risk:* medium — it refines ball-derived rallies; a spotted serve with no ball
  track afterwards does not open a rally.

**L3 · Duration-aware decoding, then a learned refinement stage** — M then L, cached

- *Hypothesis:* geometric durations put the most probability on 1 s runs, and CF-396's
  HMM also fed posteriors as emissions without dividing by the class prior. An
  explicit-duration Viterbi with durations fitted per fold, Platt-calibrated inputs and a
  per-rally objective may keep CF-396's live-play saving without its net loss. A
  minimum-duration constraint must **not** be used for rallies: 27 of 101 rallies are ≤ 3 s.
- *Experiment:* a third decoder in `decode_windows`, numpy only, chosen under Q3's nested
  selection; then, only once there are 20+ games, a small MS-TCN-style refinement stage
  reimplemented from the paper.
- *Metric:* the bar plus segmental F1@{10,25,50} and fragmentation.
- *Why it is not the rejected HMM:* different duration model, calibrated emissions, judged
  on per-rally metrics. If it loses on Q3's protocol, drop decoding work for good.
- *Invariant risk:* low (behind `condense_mode=learned`).

**L4 · Pixel-level spotting spike** — L, videos, $ — existing card CF-597 (#643)

T-DEED (GPL-3.0) or AdaSpot (MIT) trained from ImageNet on serve frames. Expect it to
underperform: every SoccerNet BAS winner pretrained on 500 games, and no public volleyball
checkpoint can ship. Worth one spike only after L1 has produced 20+ games; report it as
"does pixel spotting beat ball-residual serve detection on a gym camera", with a clear no
being an acceptable outcome.

**L5 · VLM as highlight judge** — M, videos, $ — existing card CF-600 (#646)

Fits the "which of 35 rallies is best" question better than the boundary question. Judge
three key frames or a short clip per already-accepted rally; never move a boundary. Compare
against M1 and M4 on the same fixtures before paying per match.

### 4.4 Ranking at a glance

| Rank | Item | Expected gain | Effort | Needs | Main risk |
|---|---|---|---|---|---|
| 1 | Q1 highlight re-baseline | Unblocks every highlight decision | S | test1 track | — |
| 2 | Q3 nested selection + per-rally report | Honest verdict on learned | S | caches | — |
| 3 | Q2 guarded ∪ learned | Fewer whole rallies deleted (D1, D2) | S | caches | Less dead time removed |
| 4 | Q4 no-audio fallback (CF-294) | Removes a zero-clip path | S | — | — |
| 5 | M3 persist features | Starts the ranker's data | S–M | — | Privacy review |
| 6 | Q5 gap-type rally ending | Better clip endings | S–M | caches | Touches highlight windows |
| 7 | M1 tagger cheer | Ranking (H5) | M | videos | Still court-blind |
| 8 | M2 frozen embeddings for dead time | test4-type failures | M | videos, small $ | Overfit, neighbouring court |
| 9 | M5 tracker A/B (CF-596) | Contacts, cost, licence | M | videos, $ | Cache bump if adopted |
| 10 | M4 learned ranker | Ranking | M | M3, CF-375 | Gate movement |
| 11 | M6 court filter | Multi-court junk | M | multi-court labels | Wrong polygon |
| 12 | L1 cheap labelling | More games — the real fix | L | videos, $ | Label noise |
| 13 | L3 duration decoding | Fewer fragments | M | caches | May repeat CF-396 |
| 14 | L2 ball+pose spotter | Short-rally recall | L | videos, $ | Pose cost |
| 15 | L4 / L5 | Research answers | L / M | videos, $ | Low expected gain |

---

## 5. Recommended sequence

**Wave 0 — unblock (people, days).** CF-595 (videos), D-0036 (caches), CF-593 (matcher),
CF-375 (1080p highlight fixture). Start CF-398 (two new games) now, because labelling is
the long pole. Ask a second person to re-label one existing game (L1's noise measurement).

**Wave 1 — measure honestly (cached, about a week).** Q1, Q3, then Q2 against Q3's new
reference row. Q4 (CF-294) in parallel. These change no defaults and spend nothing; they
decide whether learned is worth more work and give highlights a current baseline.

**Wave 2 — add signals through existing seams (videos, 2–4 weeks).** M3 first, since it
costs little and every week it is not running is lost data. Then M1 and M2 in parallel
(different seams: `cheer_score` vs `FEATURE_NAMES`). Q5 once CF-593 lands. M5 when a person
approves the re-track spend; it is the one that can change contacts for both paths.

**Wave 3 — learn from more data (once there are about 8–10 comparable games).** M4, L3.
L1 runs continuously from Wave 0. L2 and the CF-597/CF-600 spikes only after 20+ games.

Decision points for a person: adopting any new weights (licence row in
`docs/licensing.md`), any paid re-track, flipping `condense_mode`, and sending user video to
a third-party API.

---

## 6. Proposed board cards

Titles follow the `CF-## · Description` style; numbers are left as `CF-??` because this PR
creates no issues (next free id at the time of writing: CF-602). Existing cards that this
plan relies on are listed after.

| Proposed title | Area / labels | Depends on | Size |
|---|---|---|---|
| `CF-?? · Re-measure the test1 highlight baseline at current main (scale off/on/soft)` | eval, scoring | caches | S |
| `CF-?? · Nested model selection and per-rally metrics in train_deadtime` | eval, dead-time | caches | S |
| `CF-?? · validate_learned accepts every decoder dead_time_ml can emit` | eval, dead-time | — | S |
| `CF-?? · Eval-only condense variants: guarded ∪ learned and learned with guarded veto` | dead-time, eval | the nested-selection card | S |
| `CF-?? · Label-jitter and second-labeller noise estimate for the dead-time fixtures` | eval | a second labeller (human) | S |
| `CF-?? · Persist per-rally features, scores and gate decisions for every processed game` | scoring, api | privacy review | S–M |
| `CF-?? · Extend highlight clip ends using the ball track after the last contact (eval-only)` | ball-detection, scoring | CF-593 | S–M |
| `CF-?? · Tagger-based cheer score and audio reliability flag (EfficientAT/YAMNet)` | audio, scoring | CF-595 | M |
| `CF-?? · Per-second SigLIP 2 / PE-Core embeddings as dead-time features (eval-only)` | dead-time | CF-595, the nested-selection card | M |
| `CF-?? · Learned pairwise highlight ranker behind a default-off scorer setting` | scoring | persistence card, CF-375 | M |
| `CF-?? · Court polygon filter on cached ball positions (default off)` | ball-detection | multi-court labelled fixture | M |
| `CF-?? · One-click-per-rally labelling and boundary review page` | eval, web | CF-595 | L |
| `CF-?? · Explicit-duration Viterbi decoder with calibrated emissions` | dead-time | the nested-selection card | M |
| `CF-?? · Ball + pose keypoint spotter for serve and rally end (research)` | ball-detection, dead-time | CF-595, more labelled games | L |

Existing cards this plan relies on or extends rather than duplicates: CF-593 (#639),
CF-595 (#641), CF-375 (#475), CF-398 (#519), CF-399 (#520), CF-294 (#344), CF-596 (#642,
M5 adds VballNet and specifics), CF-597 (#643, L4), CF-598 (#644, whistle work belongs here
— gate whistles by our own ball track), CF-600 (#646, L5), CF-594 (#640 / PR #648, L1's VLM
vote), CF-397 (#518, M2's cheap baseline), CF-229 (#233), CF-376 (#476), CF-369 (#457),
CF-374 (#474).

---

## 7. Sources

Repo evidence (all on `main` @ `196822d`): `README.md` (Processing Pipeline, Key Concepts,
Known Limitations), `ARCHITECTURE.md`, `ml/eval/README.md`, `ml/eval/fixtures/README_deadtime.md`,
`ml/eval/results/*.jsonl`, `ml/pipeline/deadtime_ml_weights.json`, `docs/licensing.md`,
`docs/research/public-datasets-and-models.md`. Issues and PRs cited by number are at
`https://github.com/ClipFarmVB/ClipFarm/issues/<n>` and `.../pull/<n>`.

Tags: *read* = the primary file was opened; *excerpt* = known only through a search-engine
excerpt because the host was blocked — re-check before quoting.

**Volleyball and ball tracking**
- fast-volleyball-tracking-inference / VballNet, README incl. weight licence and results — https://github.com/asigatchov/fast-volleyball-tracking-inference (*read*); training code https://github.com/asigatchov/vball-net (*read*)
- VolleyMole and its THIRD_PARTY.md — https://github.com/rudykon/VolleyMole (*read*)
- masouduut94 volleyball analytics / models — https://github.com/masouduut94/volleyball_analytics (*read*)
- WASB-SBDT licence — https://github.com/nttcom/WASB-SBDT (*read*); BlurBall — https://github.com/cogsys-tuebingen/blurball (*read*), https://cogsys-tuebingen.github.io/blurball/
- TrackNetV4 — https://github.com/TrackNetV4/TrackNetV4 (*read*); TrackNetV3 — https://github.com/qaz812345/TrackNetV3 (*read*); TrackNetV5 SDK (weights proprietary) — https://github.com/thaonan/TrackNetV5-SDK (*read*)
- TOTNet — https://github.com/AugustRushG/TOTNet (*read*)
- Badminton rally detection, Monash — https://research.monash.edu/en/publications/a-comparative-analysis-of-deep-learning-models-and-gradient-compu/ (*excerpt*)
- CTU beach volleyball cutting thesis — https://dspace.cvut.cz/handle/10467/114627 (*excerpt*)

**Spotting and temporal localisation**
- E2E-Spot — https://github.com/jhong93/spot (*read*); T-DEED — https://github.com/arturxe2/T-DEED (*read*); AdaSpot — https://github.com/arturxe2/AdaSpot (*read*)
- SoccerNet spotting (7-game BAS, NDA) — https://github.com/SoccerNet/sn-spotting (*read*); 2023 BAS winner — https://github.com/lRomul/ball-action-spotting (*read*); team spotting — https://github.com/SoccerNet/sn-teamspotting (*read*)
- UMEG-Net (no licence) — https://github.com/LZYAndy/UMEG-Net (*read*); VNL-STES code — https://github.com/hoangqnguyen/spot (*read*)
- OSL-ActionSpotting — https://github.com/OpenSportsLab/OSL-ActionSpotting (*read*)
- ActionFormer — https://github.com/happyharrycn/actionformer_release; TriDet — https://github.com/dingfengshi/TriDet; TadTR — https://github.com/xlliu7/TadTR; OpenTAD — https://github.com/sming256/OpenTAD (all *read*)
- Moment-DETR (annotations CC BY-NC-SA) — https://github.com/jayleicn/moment_detr; UniVTG — https://github.com/showlab/UniVTG; Mr. HiSum (code NC) — https://github.com/MRHiSum/MR.HiSum (all *read*)
- SVHighlights — https://leedongkyu2019.github.io/SVHighlights/ (*excerpt*; terms unread)

**Embeddings and VLMs**
- VideoMAE (CC BY-NC) — https://github.com/MCG-NJU/VideoMAE (*read*); VideoMAE V2 model zoo (request form) — https://github.com/OpenGVLab/VideoMAEv2/blob/master/docs/MODEL_ZOO.md (*read*)
- V-JEPA 2 — https://github.com/facebookresearch/vjepa2 (*read*), https://arxiv.org/abs/2506.09985 (*excerpt*)
- SigLIP 2 — https://github.com/google-research/big_vision (*read*); Perception Encoder — https://huggingface.co/facebook/PE-Core-B16-224, https://arxiv.org/abs/2504.13181 (*excerpt*)
- InternVideo — https://github.com/OpenGVLab/InternVideo (*read*); VideoPrism — https://huggingface.co/google/videoprism-base-f16r288 (*excerpt*); DINOv3 licence — https://ai.meta.com/resources/models-and-libraries/dinov3-license (*excerpt*)
- Qwen3-VL — https://github.com/QwenLM/Qwen3-VL (*read*); Qwen2.5-VL-3B licence discussion — https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct/discussions/12 (*excerpt*)
- TennisTV (VLMs and fast rallies) — https://arxiv.org/html/2509.15602v3 (*excerpt*); ActionAtlas — https://arxiv.org/pdf/2410.05774 (*excerpt*); OnPoint (VLM as temporal labeller) — https://arxiv.org/pdf/2607.00289 (*excerpt*)
- Pricing (secondary trackers; primary pages blocked): Modal — https://modal.com/pricing, via https://www.spheron.network/blog/modal-gpu-pricing-2026-per-second-billing/; Gemini — https://ai.google.dev/gemini-api/docs/pricing, via https://pricepertoken.com/pricing-page/model/google-gemini-2.5-flash; Gemini video tokens — https://ai.google.dev/gemini-api/docs/tokens (*excerpt*)

**Audio**
- PANNs — https://github.com/qiuqiangkong/audioset_tagging_cnn (*read*); EfficientAT — https://github.com/fschmid56/EfficientAT (*read*); AST — https://github.com/YuanGongND/ast (*read*); BEATs — https://github.com/microsoft/unilm/tree/master/beats (*read*); LAION-CLAP — https://github.com/LAION-AI/CLAP (*read*); MS-CLAP — https://github.com/microsoft/CLAP (*read*); YAMNet class map — https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv (*read*)
- Referee whistle detection (92.5% sensitivity) — https://www.amrita.edu/publication/automated-referee-whistle-sound-detection-for-extraction-of-highlights-from-sports-video/ (*excerpt*)
- RoboCup whistle vs crowd — https://arxiv.org/pdf/2411.17347 (*excerpt*)
- Audio highlight study, "loudness alone is insufficient" — https://arxiv.org/abs/2609.17923 (*excerpt*)
- SoccerNet audio + video (+4.19 mAP spotting) — https://arxiv.org/abs/2011.04258 (*excerpt*)
- Modality dropout for irrelevant audio — https://arxiv.org/abs/2203.03014 (*excerpt*)
- Volleyball whistles dataset — https://huggingface.co/datasets/GYdevy/volleyball-whistles (per CF-601's read on 2026-11-05)

**Weak supervision, smoothing, evaluation**
- Snorkel — https://github.com/snorkel-team/snorkel (*read*); FlyingSquid — https://github.com/HazyResearch/flyingsquid (*read*), https://proceedings.mlr.press/v119/fu20a.html (*excerpt*)
- Timestamp supervision (Li et al., CVPR 2021) — https://openaccess.thecvf.com/content/CVPR2021/papers/Li_Temporal_Action_Segmentation_From_Timestamp_Supervision_CVPR_2021_paper.pdf (*excerpt*)
- Boundary-only active learning (B-ACT) — https://arxiv.org/pdf/2604.15173 (*excerpt*)
- Label Studio TimelineLabels — https://labelstud.io/tags/timelinelabels (*excerpt*); licence https://github.com/HumanSignal/label-studio (*read*)
- MS-TCN (Commons Clause licence) — https://github.com/yabufarha/ms-tcn (*read*), paper https://arxiv.org/pdf/1903.01945 (*excerpt*); ASRF — https://github.com/yiskw713/asrf (*read*)
- Segmental metrics (Lea et al.) — https://arxiv.org/pdf/1611.05267 (*excerpt*)
- Cross-validation error bars (Varoquaux 2018) — https://arxiv.org/pdf/1706.07581 (*excerpt*); cluster bootstrap with few clusters — https://arxiv.org/pdf/2411.01723 (*excerpt*)
- scikit-learn calibration guide — https://scikit-learn.org/stable/modules/calibration.html (source *read* on GitHub)
