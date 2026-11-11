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
issue and PR). Five more surveyed outside work. A separate reviewer then challenged the
draft against the code and the results files, and this version incorporates its findings,
including one new derived number (§4.1 Q1). **Source caveat:** the session's network refused
arxiv.org, huggingface.co, openaccess.thecvf.com, zenodo.org, modal.com and ai.google.dev.
Licences marked *read* were read from the LICENSE or README file on GitHub. Paper numbers and
prices come from search-engine excerpts and are marked *(excerpt)*. Re-read the primary page
before you quote one in a PR or act on a licence row — the same rule
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
  contact-floor fix (CF-103) and the resolution scaling (CF-174), so today's number is
  unknown. Two structural problems on top: a video with no usable audio produces zero
  clips, and ranking among the survivors is weak (README Known Limitations).
- **Dead time.** At the CF-174 switch position the learned model was fitted at (*on*),
  the shipped `guarded` builder and the unvalidated `learned` builder land within about
  5 net-seconds of each other on the three comparable games, and `learned` fails the
  CF-389 bar on live play cut. **The dominant error for both is deleting short rallies
  outright (1–4 s serves, aces, net serves), not trimming edges**, and the two builders
  lose largely *different* rallies.
- **A cheap result already in the committed rows.** Because the stored audit spans are
  complete, the union of the guarded and held-out learned keep-windows can be scored
  without running anything: **+547.5 s net with 102.3 s of live play cut, against
  guarded's +458.4 s / 180.1 s** on the comparable three (switch on). It deletes 16 whole
  rallies instead of 26. That is derived arithmetic with known caveats (§4.1 Q1), not a
  validated builder, but it is the strongest lead in this plan.
- **Data.** There are three comparable labelled games (about 41 min, 101 rallies, one
  labeller) and one 360p highlight fixture. The fixture source videos were deleted by the
  R2 retention sweep (CF-595), and the ball caches exist only on one machine (D-0036).
  Every experiment that needs a new *signal* (audio, pixels, pose) is blocked until the
  videos come back. With three games, no game-level claim is statistically supportable;
  the plan uses rally-level paired tests and treats game-level conclusions as judgement
  calls (§4 protocol).

**Recommended order, in one line each** (detail in §5):

1. **Measure honestly, on cached tracks (Wave 1).** Confirm the union result properly,
   remove the model-selection leak, add per-rally metrics, and evaluate the cheap
   cached alternatives already on the board: the static-site filter for dead time, v3
   (CF-586), the soft-scale exponent (CF-376), and a duration-aware decoder.
2. **Unblock (people, in parallel).** Recover the videos, put the caches where the loop
   runs, land the per-rally matcher, label a 1080p highlight fixture and new venues.
3. **Add signals through existing seams (Wave 2), each behind a default-off flag:**
   tagger-based cheer, end-of-rally confirmation from the ball track and whistles, an
   offline A/B of a self-hosted volleyball tracker, frozen image embeddings for dead time,
   and a feature log a learned ranker can later train on.
4. **Learn from more data (Wave 3):** cheap labelling (one click per rally, a VLM as one
   weak vote), a learned ranker once five or more highlight-labelled games exist, and a
   small ball+pose spotter.

What the plan **does not** recommend: training end-to-end video models on our data (too
little), using a VLM to set rally boundaries (published results say they are weak at
fine-grained timing), lowering `MIN_RALLY_CONTACTS`, more padding, or adding audio as
features to the in-play classifier. Each was measured before and lost (§2.4). Action
labels (CF-3) and the pose refinement stage are out of scope: pose spends about 5.6 s of
T4 per rally on labels only and does not affect selection or ranking.

---

## 2. Current state and failure modes (with evidence)

### 2.1 Pipeline as it runs today

| Stage | What runs | Key constants | Where |
|---|---|---|---|
| 0 Audio | ffmpeg → 16 kHz mono → RMS envelope (0.5 s window, 0.1 s hop) | — | `ml/pipeline/audio.py:93, 137` |
| 1 Ball | Roboflow RF-DETR `volleyball-ball-tracking-0eo7r/3` on a Modal T4, about 3 looks/s (`round(fps/3)`; the `SAMPLE_EVERY = 3` module default is not what production passes), batch 1, `MIN_CONF` 0.40, single-hypothesis nearest-prediction linker | — | `ml/pipeline/ball.py:494` `track_ball`, `ml/modal_app.py` |
| 1 Contacts | Track segmentation on implausible motion, gravity estimated per video, contact = ballistic residual above a speed-scaled floor | residual floor 240 px/s (CF-103); CF-174 height scaling **off in production** since 2026-10-19 | `ball.py:1042` `find_contacts` |
| 1 Rallies | Group contacts on gaps, pad, cap clip length | gap 5 s, pads 2.0 s / 2.5 s, `MIN_RALLY_CONTACTS = 3`, `MIN_RALLY_DURATION = 2`, 30 s cap split on largest gap | `ball.py:326-333`, `:1340` |
| 2 Score + gate | `0.45·cheer + 0.35·shape (+0.20·CLIP)`; CLIP is off and cannot load in the worker image, so the effective score is `0.5625·cheer + 0.4375·shape`; gate 0.50 | cheer = peak RMS in `[last_contact, +4 s]`, scaled `(peak − median)/(p99 − median)` per video | `score.py:25-27, 99-125`, `audio.py:35-38` (constants), `score_cheers` `audio.py:154` |
| 3 Pose | YOLOv8s-pose @ 1280 every 4th frame, on Modal, inside gate survivors only; changes action labels only | — | `detect.py:456`, `ml/modal_pose.py` |
| 4–5 | Audio confidence weighting (`weight_detections_by_audio`, `audio.py:198`); ffmpeg cut at the padded times | — | `clip.py:60` |
| 6 Condense (opt-in) | Keep-windows from the **pre-gate** stage-1 signal. `condense_mode` ∈ `rules` / `guarded` (default) / `learned` (off; falls back learned → guarded → rules) | guarded: speed gate 0.25 fh/s, motion anchor 0.30 fh/s, pads 3/2, merge 3, abstain below 1.0 usable samples/s | `dead_time.py:507-606`, `dead_time_ml.py`, `api/app/workers/tasks.py:212-300` |

Compute (repo's own figures): tracking about 16–19 s per video-minute on the T4; pose about
5.6 s per rally; GPU cost "roughly $0.25 per hour of footage" (`api/app/config.py:637`).
The worker image carries **no ML dependencies** (`Dockerfile.api`, CF-164): anything torch-
or TensorFlow-based runs on Modal. Roboflow credits already exceed the free tier (CF-369),
and the RF-DETR weights' licence is unknown (CF-374).

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
6. **360p `test1` is the invariance control:** the CF-174 scale is exactly 1.0 there, so a
   resolution-scaling change must leave it bit-identical — and cannot be *evaluated* on it.
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
| H3 | **The 3-contact rule is a cliff.** Short rallies, aces and kills off serve-receive have ≤ 2 detected contacts. | With CF-174 scaling on, production clips fell 34 → 11; 51 of 64 lost 1080p rallies died at the count gate (CF-376). Production now runs the scaling off; a soft scale (PR #613) awaits an exponent choice. |
| H4 | **No usable audio means zero clips.** With cheer = 0 the best possible score is 0.4375 < 0.50. | A video with no audio track, or flat/silent audio, gates out every rally while the stage reports success (CF-294). A *quiet* gym is not the problem — cheer is normalised per video — but a loud neighbouring court passes. |
| H5 | **Ranking is weak.** Cheer cannot tell a great rally from a loud celebration or a funny error. | README Known Limitations. A 2026 MMSports study found highlight clips are louder on average but the distributions overlap heavily: "loudness alone is insufficient" *(excerpt, [arXiv 2609.17923](https://arxiv.org/abs/2609.17923))*. |
| H6 | **Clip ends are fixed pads on contacts.** Nothing detects the ball landing or the whistle, so a missed last contact truncates the ending; the 30 s cap cuts the longest rallies into separately scored pieces. | `ball.py:1257-1258, 1311-1336`; CF-38 (#47) is open and the highlight path still lacks its polish. |
| H7 | **No learning loop.** Rally features and gated-out rallies are not persisted, so a learned ranker has nothing to train on. | `tasks.py:1423-1435`; the only signals stored are `Correction` relabels and `Post.like_count`. |

**Dead-time path** (comparable three = test2, test4, test5; numbers from the committed rows)

| Builder | Switch | Summed net s (4:1) | Summed live cut s | Status |
|---|---|---|---|---|
| rules | on | +201 | 196 | fallback |
| rules | **off** | +134 | 85 | fallback |
| guarded | on | +458.4 | 180.1 | **default** |
| guarded | **off (production)** | +533 | 132.9 | **default** |
| guarded v3 (eval ladder) | on | +459 | 88 | CF-586, needs a decision |
| learned, held out | on (fitted at on only) | +463.3 | 210.5 | off; `validated: false` |
| guarded ∪ learned (derived, §4.1 Q1) | on | +547.5 | 102.3 | not built |

At the production position the default cuts about 48 s *more* live play than the `rules`
fallback; it was shipped as a judgement call at the 4:1 rate (CF-187).

| # | Failure | Evidence |
|---|---|---|
| D1 | **Live-play harm is mostly whole short rallies deleted**, not edges trimmed. | From the stored `over_cut_live` spans: guarded on test4 cuts 115 s, of which 60 s are 14 whole rallies (lengths 1–12 s); learned cuts 147 s, 127 s of it 23 whole rallies. Interior cuts are about 0 on the comparable three (test1 has 5–6 s). 27 of the 101 comparable rallies last ≤ 3 s. |
| D2 | **Guarded and learned lose different rallies.** | test4: 10 deleted by both, 4 by guarded only, 13 by learned only; test5 3/5/7; test2 3/1/4. Across the three: 10 guarded-only vs 24 learned-only discordant rallies. |
| D3 | **The learned model fails its bar, and the pass it has is biased.** | CF-395: clause 1 (net) passes by +4.92 s, but the context variant was chosen on the same held-out folds (the other variant fails by −23.6 s); clause 2 fails (210.5 vs 195.1 s); clause 3 fails on test4 (+31.4 vs +30 s). All learned evidence is at switch *on*; production runs *off*. |
| D4 | **test4's track is the bottleneck, not the classifier.** | Held-out AUC 0.728 on test4 against 0.914–0.947 on test1/2/5 (test3 0.816); 19 of its 46 rallies produce no contact (CF-187); 56% of its samples sit on static objects (CF-229). |
| D5 | **Tiny n, one labeller, no uncertainty.** | The bar's margins are one rally (a 7 s rally cut whole costs 28 net-seconds). 1 s label quantisation over about 200 endpoints is larger than the bar's 15 s slack. No inter-annotator measurement exists. test1 (360p, different labeller) is 54% of the training rows. |
| D6 | **Features see only the ball track.** | 13 ball-track features (`dead_time_ml.py:84-107`). Nothing sees players walking back, a huddle, a ready stance or the referee. |
| D7 | **Seconds-weighted metrics hide the dominant error.** | No per-rally metric on `main` (CF-593's matcher is not merged); a 2 s serve deleted whole costs the same as 2 s trimmed off a 20 s rally. `validate_learned.KNOWN_DECODERS` (`:116`) still lists only `threshold`, so an HMM model cannot be validated. |

### 2.4 Already tried — do not re-propose without new evidence

Condensed from the history notes; each row is a measured result, not an opinion.

| Idea | Result | Where |
|---|---|---|
| More padding to protect live play | Every step past 5/4 costs 4–32 dead seconds per live second saved | CF-103 |
| Lowering or scaling `MIN_RALLY_CONTACTS` | Cannot recover segments that never form, admits junk; cause is upstream contact recall | CF-376 |
| One-knob or multi-knob contact sweeps fitted to 2–3 games | Masking between knobs; < 1% gain for double the knobs | CF-103, PR #182 |
| Learned in-play classifier on 1–2 games | Cut 100% of play on held-out test2 — learned the venue | CF-173, PR #179 |
| Two-state HMM decoding (geometric durations, raw posteriors, net-only objective) | Lost net (+341 vs +463 s) while cutting less play (148.5 vs 210.5 s) | CF-396 |
| ±context lags | Lower held-out net than none | CF-393 |
| Audio as features for the in-play classifier | 0.738 AUC alone; 0.914 → 0.916 added — redundant | CF-389 epic |
| Pose gate for dead time | Overfits: tuned +68 s, held-out −63 s | CF-198 branch |
| Pose anchor for dead time | Held-out +4 s net and 39% less live cut, but needs full-video pose, about 15.6 GPU-min per game-hour | CF-198 branch |
| End-to-end video fine-tuning | Ruled out: "112 labelled minutes is far below what fine-tuning needs" | CF-389 epic |
| Lowering detector confidence for non-Mikasa balls | Added positions no better than chance | CF-171 |
| Motion bridge for condense | Net-negative; collapses when the bridge max exceeds the inter-rally gap | CF-187 |

Note the difference between these and what §4 proposes. Audio is rejected as *in-play
features*, not as a *boundary* or *cheer* signal. HMM is rejected with geometric durations
and a net-only objective, not as a class. Video is rejected as *end-to-end training*, not as
*frozen embeddings with a linear probe*. The pose anchor is the closest precedent for M3
(frozen embeddings), and M3 says how it differs. Each §4 item that sits next to a rejected
idea says why it is different.

---

## 3. What the outside world offers, and what it would buy us

One line per family. Detail and full links are in §7.

| Family | Best candidates (licence) | What it buys ClipFarm | Fit at our data scale |
|---|---|---|---|
| **Volleyball ball trackers** | VballNet / `fast-volleyball-tracking-inference` (MIT code **and** ONNX weights, *read*; **training data unstated**); WASB-SBDT (MIT code; volleyball weights trained on unstated data); TrackNetV3/V4 (MIT) | A self-hosted tracker cheap enough to run on every frame (10× today's samples at 30 fps, 20× at 60 fps), removing Roboflow credits (CF-369). VballNet's author reports F1 0.90 at about 150 fps on an i5 CPU, on 886 beach frames — own numbers, tiny and off-domain. Both carry the same unstated-data caveat, so under CF-601's buckets they are fine to *evaluate*, not yet to ship. | No training needed for an A/B; fine-tuning via BlurBall's MIT training scripts if it misses. |
| **Volleyball pipelines** | VolleyMole (MIT code; weights carry no licence); masouduut94 VideoMAE SERVICE/PLAY/NO-PLAY (GPL-2.0 app repo, weights and data unstated) | Architecture references: fuse a clip-level game-state classifier with the ball track; rank with rules or a VLM. Nothing shippable as weights. | Copy the idea, not the weights. |
| **Precise event spotting** | E2E-Spot (BSD-3), T-DEED (GPL-3.0, won SoccerNet Ball Action Spotting 2024), AdaSpot (MIT); VNL-STES adapts the family to volleyball at 8 games | Serve and rally-end timestamps from pixels, independent of the ball track. | Marginal: every SoccerNet BAS winner pretrained on the 500-game set first. Public checkpoints are trained on NDA, NC or YouTube footage, so none can ship. |
| **Temporal action localisation** | ActionFormer, TriDet (MIT), TadTR, OpenTAD (Apache-2.0) | A learned rally/dead-time segmenter over frozen features. | A light head on frozen features is realistic; boundary precision at 0.5–1 s feature stride may trail ball residuals. |
| **Keypoint-graph spotting** | UMEG-Net (AAAI 2026, few-shot; **no LICENSE file**, so reimplement) | A spotter over the ball and pose tracks we already compute. | Designed for 25–100 clip training sets *(excerpt)*. |
| **Image / video embeddings** | SigLIP 2, Perception Encoder (PE-Core), DINOv2, VideoPrism (Apache-2.0); V-JEPA 2 (MIT code; weights "MIT" per a secondary source) | Per-second visual features: players walking vs ready, huddles, ball retrieval — what the ball track cannot see. | Linear probe on frozen features is realistic on 134 spans. |
| **Avoid for anything that ships** | VideoMAE v1 and official VideoMAE V2 weights (CC BY-NC 4.0); Qwen2.5-VL-3B (research licence); MS-TCN official code (MIT + Commons Clause, AGPL-incompatible); SoccerNet video (NDA); QVHighlights annotations (CC BY-NC-SA) | — | Eval only, or reimplement from the paper. |
| **Audio taggers** | EfficientAT (MIT; mn10 0.471 AudioSet mAP at 0.54 GMACs per 10 s), YAMNet (Apache-2.0), PANNs (MIT code; weights probably CC BY 4.0, unverified), BEATs (MIT) | Separate cheer, whistle, ball-hit, squeak and music, which RMS lumps together. AudioSet already has Whistle, Cheering, Applause, Crowd, Shout, Whack/thwack and Squeak classes. Small enough for CPU, but the worker has no ML deps, so it runs on Modal or via an approved ONNX runtime. | No training to get a first signal. |
| **Whistle detection** | DSP band-pass detectors: 92.5% sensitivity on 12 h of broadcast audio (2011); `GYdevy/volleyball-whistles` (MIT on its card) | Rally start (serve whistle) and end (point whistle) where a referee exists. | **Neighbouring courts' whistles will bleed in**; no source found on separating them. Optional and gated by our own ball track. |
| **VLMs** | Gemini 2.5 Flash-Lite / Flash, Claude Haiku, self-hosted Qwen3-VL-8B (Apache-2.0 repo) | A coarse judge of "is this 10–40 s gap dead?" and an offline labeller. Estimated $0.02–$0.42 per match for 100 ambiguous 20 s gaps; ~$12–53 to label 50 matches (§4.3 L1, prices *(excerpt)*). | Weak at timing: TennisTV notes fast rallies need dense sampling, and Gemini 2.5 Flash as a temporal labeller scored 29.2 vs 90.3 mAP on THUMOS14 in one 2026 paper *(excerpt)*. Use per segment, never for boundaries. |
| **Weak supervision / labelling** | One click per segment ("timestamp supervision": 97% of full supervision on 50Salads *(excerpt)*); boundary-only active learning (B-ACT 2026 *(excerpt)*); Label Studio TimelineLabels (Apache-2.0); FlyingSquid temporal label model (Apache-2.0, unmaintained — reimplement) | The fastest route past three comparable games. | The point of the exercise. |
| **Smoothing / decoding** | Explicit-duration (semi-Markov) Viterbi; hysteresis; MS-TCN-style refinement (reimplement) | Fewer fragments. MS-TCN on 50Salads: frame accuracy 78.2 → 80.7 while segmental F1@10 went 27.0 → 76.3 *(excerpt)* — AUC hides fragmentation. | Duration models are cheap; a learned refinement stage overfits below ~20 games. |
| **Evaluation at tiny n** | Rally-level paired tests (McNemar on discordant rallies); segmental F1@IoU and edit score; Platt calibration over isotonic below ~1000 samples (scikit-learn docs, *read*) | A verdict we can defend with 3 games. A sign test over 3 games can never reach p < 0.125; a cluster bootstrap over games has poor coverage below ~15–20 groups *(excerpt)*; fold standard errors understate the true spread (Varoquaux 2018 *(excerpt)*). | Free. |

---

## 4. Ranked candidate improvements

Effort: **S** ≤ 2 days, **M** ≤ 1–2 weeks, **L** multi-week. "Cached" means the experiment
runs on the five local ball caches with CPU only; "videos" means it needs CF-595's recovered
fixture videos; "$" means it needs paid compute a person must approve.

### The standard protocol (every dead-time experiment)

- **Nested leave-one-game-out.** Every choice (feature subset, context, decoder, threshold,
  pads) is made inside the fold. Inner objective: summed net over the *training* games,
  **weighted per game, not per row** (so test1's 3660 s does not dominate), with test3
  counted only through its abstain clause. The outer game only scores.
- **Switch position.** Report at the production position (CF-174 scaling **off**) and at
  **on**. A learned model is refitted at the position it is scored at; the existing folds
  were fitted at *on* only.
- **Paths.** Rule builders are added to `VARIANTS` (`ml/eval/deadtime_variants.py`) and
  scored by `visualize_deadtime`. Anything involving held-out learned windows goes through
  `validate_learned --dump-windows` (per-fold weights) → a combine step → `harness --mode
  deadtime --windows-json`, because `VARIANTS` builders only see the committed in-sample
  weights.
- **Per-rally metrics next to seconds.** Rally recall at "≥ 50% kept" and "100% kept",
  whole rallies deleted by length bucket (≤ 3 s, 3–8 s, > 8 s), start and end error.
- **Statistics, decided before looking.** Primary test: McNemar on discordant whole-rally
  deletions against `guarded` (e.g. the existing 10 guarded-only vs 24 learned-only gives
  two-sided p ≈ 0.024). Secondary: summed Δnet and Δlive with a within-game block bootstrap
  over (rally + following gap) units — descriptive only, since it ignores between-game
  variance. **Game-level conclusions ("generalises to new gyms") are judgement calls at
  n = 3 and must be labelled as such.**
- **Sensitivity.** Rerun with every label endpoint jittered ±1 s; a result that flips is
  not a result.

### 4.0 Prerequisites (mostly existing cards — listed so the order is explicit)

| Item | Card | Why it gates the rest |
|---|---|---|
| Recover fixture source videos to a retention-exempt prefix | CF-595 (#641) | Every new-signal experiment needs pixels or audio. Highlight `--offline` re-downloads video for audio. |
| Ball caches where the research runs | D-0036, CF-593 (#639) | Every cached experiment. |
| Rally precision / recall / boundary-error matcher at 1080p | CF-593 (#639) | The common per-rally scorer. |
| A 1080p highlight fixture | CF-375 (#475) | The only highlight fixture is 360p; no 1080p highlight change has ever been measured. |
| Two new-venue labelled games | CF-398 (#519) | Turns "3 games" into 5; adds a lock-box test. |

### 4.1 Quick wins (cached, CPU, no new dependencies)

**Q1 · Guarded ∪ learned keep-windows** — S, cached

- *Derived result already in hand.* The committed `over_cut_live` and `missed_dead` spans
  are stored in full (they sum exactly to `live_removed_sec`). Combining the
  `CF-391-rebaseline` (guarded, switch on) and `CF-395-learned` (held out) rows: a second
  is cut only if both cut it, and dead time is missed if either keeps it.

  | Game | guarded live / net | union live / net |
  |---|---|---|
  | test2 | 14.1 / +175 | 10.6 / +139.8 |
  | test4 | 115.1 / +275 | 75.3 / +320.2 |
  | test5 | 51.0 / +7.8 | 16.5 / +87.6 |
  | **Sum** | **180.1 / +458.4** | **102.3 / +547.5** |

  Whole rallies deleted fall from 26 to 16. Against guarded this would pass bar clauses
  1–3. Mixing guarded at switch *off* with the same learned folds gives +538.6 s / 81.7 s,
  against guarded-off's +533 / 132.9 — indicative only, because the learned folds were
  fitted at *on*.
- *Caveats.* Inherits CF-395's variant-selection bias; three games; the learned side used
  the same contacts at switch on. This is arithmetic on stored spans, not a builder run.
- *Experiment to confirm.* Under the standard protocol after Q2: refit learned at both
  switch positions, dump held-out windows, combine, record with `harness --windows-json`.
  Add a second arm, "learned with guarded veto" (never cut inside a guarded contact or
  anchor window), and compare both against v3 (Q3) and `rules`. Guarded's `Abstained`
  verdict must win over any learned window (test3).
- *Metric.* Pre-registered: McNemar on whole-rally deletions vs guarded significant at
  0.05, summed live cut below guarded's at both switch positions, net ≥ guarded's.
- *If it holds.* A default-off `condense_mode="union"` card; flipping it is a person's call
  (CF-399).
- *Invariant risk:* low — keep-window semantics unchanged.

**Q2 · Nested selection, per-rally reporting and an honest learned verdict** — S, cached

- *Hypothesis:* the learned builder's one passing clause is selection-biased (D3); fixing
  that and reporting per rally gives an honest answer and the reference row every later
  dead-time card compares against.
- *Experiment:* in `ml/eval/train_deadtime.py`, move `choose_variant` / `choose_decoder`
  into an inner LOGO loop with the inner objective defined in the protocol; refit at
  switch off and on; add the per-rally metrics and McNemar table to the fold record;
  update `validate_learned.KNOWN_DECODERS` and `fold_weights` so the HMM decoder (and L-
  series decoders) can be validated. Re-run the bar.
- *Metric:* the CF-389 bar on unbiased folds at both switch positions.
- *Invariant risk:* none in production; eval only.

**Q3 · Settle v3 vs v5 (CF-586) with per-rally metrics** — S, cached, existing card CF-586 (#611)

- *Hypothesis:* at switch on, v3 ties guarded on net (+459 vs +458 s) with about half the
  live cut (88 vs 180 s); the per-rally view will show whether that is fewer whole-rally
  deletions or just less aggression. It is the cheapest dead-time improvement already on
  the board.
- *Experiment:* `visualize_deadtime` at switch off and on with Q2's per-rally report;
  include Q1's union as a third column.
- *Metric:* as Q1. *Risk:* v3 was designed on test2/test4 (TUNED_ON), so test5 carries
  the verdict.

**Q4 · Static-site filter as a dead-time candidate** — S, cached

- *Hypothesis:* test4's polluted track (D4) causes most of its live cut. The CF-229 filter
  (PR #616, default off) drops static share there from 56.3% to 7.7%, runs after the cache
  (no re-track), and on test1 already cut rules live cut 176 → 136 s and guarded 126 →
  117 s. It has not been scored on the comparable three for guarded, v3 and learned.
- *Experiment:* `visualize_deadtime --static-site-filter` × switch off/on for guarded and
  v3; learned refitted with the filter on (Q2's trainer). Highlight side: pre-gate rally
  recall on test1 and, once CF-593 lands, on test2–5 — the contact set is shared.
- *Metric:* test4 whole-rally deletions and live cut down without regressing test2/test5;
  contacts per human rally closer to the label count.
- *Invariant risk:* low (flag exists, post-cache). Changes both paths — score both.

**Q5 · Choose the CF-376 soft-scale exponent** — S, cached + CF-593, existing card CF-376 (#476)

- *Hypothesis:* the sanctioned way past the 3-contact cliff (H3) is not lowering the count
  but scaling the contact thresholds softly; the soft scale exists (PR #613, exponent 0.5,
  default off) and only lacks labels to pick the exponent.
- *Experiment:* sweep the exponent (`tune_contacts --loo --exponents`) on test2–5 once the
  CF-593 matcher is in; score rally recall and all three condense builders, because the
  contact set feeds both paths. Note CF-416 (#547): the tuner scores `rules`, so read the
  `--loo` guarded columns.
- *Metric:* rally recall at 1080p up, condense live cut not up; test1 bit-identical.

**Q6 · Highlight baseline re-measure, cached half** — S, cached, existing card CF-97 (#97)

- *Hypothesis:* the 18.9% captured figure is from July and the tracking/contacts have
  changed since; every highlight decision below needs a current number.
- *Experiment:* from test1's cached track, dump pre-gate rallies and score with
  `harness --test test1 --clips-json`, static-site filter off and on. (CF-174's switch is a
  no-op at 360p, so do not sweep it here.) Post-gate numbers and Score AUC need cheer, hence
  audio, hence the video — that half (Q6b) waits on CF-595 and then also sweeps the gate
  0.50 vs 0.55, which closes CF-2 (#26).
- *Metric:* Captured % and well / butchered / missed of 41, pre-gate.

**Q7 · Silent zero-clip paths** — S, existing cards CF-294 (#344) and CF-230 (#237)

- *Hypothesis:* renormalising the score over the remaining terms when audio is missing or
  flat — as `score.py` already does when CLIP is absent — and treating a zero-contact ball
  run as a failure that triggers the pose fallback remove two "ready, 0 clips" outcomes
  without changing normal footage.
- *Experiment:* a test1 run with the audio stripped (should now produce clips) and a
  normal run (bit-identical rows).
- *Invariant risk:* low; both are existing patterns.

**Q8 · Duration-aware decoder** — S–M, cached

- *Hypothesis:* CF-396's HMM used geometric durations (most probability on 1 s runs) and
  raw posteriors as emissions, with `POS_WEIGHT = 3.0` skewing them further. An
  explicit-duration Viterbi with durations fitted per fold, posterior ÷ prior emissions
  and Platt-calibrated inputs may keep CF-396's live-play saving without its net loss.
  Durations must allow 1–3 s rallies (27 of 101 are ≤ 3 s): a *minimum* rally duration is
  exactly the wrong prior here.
- *Experiment:* a third decoder in `decode_windows`, numpy only, chosen under Q2's nested
  selection; also a two-threshold hysteresis decoder as the trivial baseline.
- *Metric:* the bar plus segmental F1@{10,25,50} and fragmentation.
- *Why it is not the rejected HMM:* different duration model, calibrated emissions, judged
  per rally. If it loses under Q2's protocol, stop decoder work until there are 20+ games.

### 4.2 Medium bets (need the videos)

**M1 · Tagger-based cheer and an audio reliability flag** — M, videos, small $

- *Hypothesis:* replacing peak RMS with the posteriors of AudioSet's Cheering, Applause,
  Crowd, Shout and Yell classes in the same post-contact window stops whistles, ball slams,
  music drops and squeaks from counting as cheer, and improves ranking (H5). The same pass
  yields a reliability flag (music fraction, clipping, silence) for Q7's renormalisation.
- *Model and placement:* EfficientAT mn10 (MIT) or YAMNet (Apache-2.0) on Modal — the
  worker image has no ML dependencies (CF-164). An ONNX export with `onnxruntime` in the
  worker is the alternative, as a new dependency a person approves. Compute is small by MAC
  arithmetic *(estimate, not benchmarked)*. Record the weight licence in
  `docs/licensing.md` before merging.
- *Experiment:* recompute `cheer_score` for test1 (and the CF-375 fixture) with both
  scorers; highlight harness Score AUC and Captured %, pre-gate and post-gate. Keep the
  `cheer_score` field and 0–1 range so `score.py` is untouched.
- *Metric:* Score AUC up with Captured % not down, and M above C in rank, on both fixtures.
  One fixture alone is a direction, not a result.
- *Why it is not the rejected audio idea:* CF-389 rejected audio as *in-play features*.
  This is the highlight *ranking* signal, which audio already is.
- *Invariant risk:* low. Still court-blind; does not create rallies.

**M2 · Better clip ends: track-after-last-contact and whistle confirmation** — M, cached then videos; extends CF-38 (#47) and CF-598 (#644)

- *Hypothesis:* a track gap has three causes — detector miss (short, mid-air), ball out of
  frame (ends at the top edge), ball dead (ends low, slows, rolls). Ending a rally at the
  last contact + 2.5 s ignores which, and truncates endings (H6). Step 1 (cached): find
  the last *in-play* track point after the last contact using gap type; `track_calculator.py`
  in `fast-volleyball-tracking-inference` (MIT) is a working example of rolling-ball and
  "ball handed over for the next serve" trimming. Step 2 (videos): accept a whistle within
  a few seconds of that point as confirmation, never as a rally on its own (neighbouring
  courts). Apply `POST_PLAY_PAD` *after* the refined end, so the celebration stays in the
  clip.
- *Experiment:* CF-593 end error for the refined *ball* end (before padding); the
  highlight harness's "butchered" bucket on test1 for the padded clip. A cheap side check
  on H6: score and cut pre-split > 30 s groups as one clip and see whether test1's long
  M-tier rallies stop being butchered.
- *Metric:* median end error and butchered count down; condense rows unchanged (this
  moves only the highlight window end, not contacts or rally membership).
- *Invariant risk:* medium-low; needs `volleyball-whistles` and one multi-court video to
  check the whistle step before it may move a boundary.

**M3 · Per-second frozen image embeddings as a dead-time veto/boost** — M, videos, small $

- *Hypothesis:* the learned model's errors are on rallies the ball track cannot see (D4,
  D6). A frozen image encoder sees players in ready stance, walking back, huddling.
- *Model:* SigLIP 2-B/16 or PE-Core-B/16 (both Apache-2.0) at 1 fps; cost estimated at
  $0.03–0.05 per match on an L4/T4, dominated by decoding *(estimate)*. A Modal function
  writes `embeddings/{md5}-{model}-1fps.npy` next to the ball cache, once per video.
- *Experiment:* PCA to ≤ 16 dims fitted inside each fold; ablation ball-only /
  embedding-only / both under the standard protocol. Combine so the invariant holds: the
  embedding may **lower** in-play probability anywhere, but may **raise** it only in
  seconds that have ball-track samples (it cannot open a window over a dense, still track
  on our court or over a neighbouring court with none of our samples). Run CF-397's cheaper
  frame-difference features first as the baseline to beat.
- *Comparator:* the CF-198 pose anchor, rerun at low resolution on the same videos.
- *Why it is not the rejected pose anchor:* it is the same shape — a full-video GPU pass —
  so the argument is cost and evidence, not kind. At $0.03–0.05 per match it is roughly
  3–5× cheaper than full-video pose (≈ 15.6 T4-min per game-hour, ≈ $0.15 at the T4 rate),
  but the pose anchor already has held-out evidence (+4 s net, 39% less live cut) and M3
  has none. Rank them by the comparator result; if the pose anchor wins at low resolution,
  revive CF-198 instead.
- *Why it is not the rejected video idea:* no fine-tuning; one logistic layer over frozen
  features. Data need is the same as today's model.
- *Metric:* McNemar on whole-rally deletions vs the Q1/Q2 reference; test4 deletions down.
- *Invariant risk:* medium, contained by the combination rule and `condense_mode=learned`.

**M4 · Offline A/B of a self-hosted volleyball ball tracker** — M, videos; extends CF-596 (#642) and CF-175 (#181)

- *Hypothesis:* VballNet (or WASB) run on every frame gives fewer identity hops (H2), more
  contacts on short rallies (H3, D1), and lowers the Roboflow cost (CF-369).
- *Design:* three arms so model and rate are not confounded — (a) VballNet at every frame,
  (b) VballNet decimated to today's ~3 looks/s, (c) RF-DETR at 10 looks/s (CF-175, the
  rate-only arm; this one needs approved Modal spend). Arms (a) and (b) can run locally on
  CPU at the author's ~150 fps — no paid compute. Each writes the same `[BallPosition]`
  contract under a separate cache key; replay `find_contacts`, `contacts_to_rallies` and
  the condense builders. CF-42's frame-skip saving is lost in (a), so measure decode time.
- *Metric:* `track_quality.py` hop rate and static share; CF-593 rally recall; condense
  rows; no regression on test1. Read the contact tuner's guarded columns (CF-416).
- *Licence:* evaluation only until VballNet's training-data provenance is known.
- *Invariant risk:* high if adopted — a new tracker means a `TRACKING_CACHE_VERSION` bump
  and re-tuning the residual floor. Stays eval-only until it wins on every fixture.

**M5 · Persist per-rally features, scores and gate decisions** — M, no $

- *Hypothesis:* a learned ranker and every future scorer experiment need labelled examples
  joined to the features the pipeline actually computed; today they are discarded (H7).
- *Experiment:* store each pre-gate rally's `features`, `cheer_score`, score and gate
  decision per game; add event tracking for the user actions that would become weak labels
  (download, share, delete — none are recorded today). That needs an Alembic migration on
  the linear chain (coordinate per CLAUDE.md) and a privacy review. Reporting-never-breaks:
  a failed write logs and continues.
- *Metric:* share of processed games with a complete feature log.
- *Invariant risk:* none to detection.

**M6 · Court polygon filter applied after the cache** — M, needs a multi-court fixture; relates to CF-29 (#45, closed)

- *Hypothesis:* README calls multi-court junk "the top open detection task". CF-29 was
  closed as completed, but no court-ROI code is on `main` (only a pose x-band filter). A
  user-confirmed court polygon applied to ball positions after the cache, like
  `drop_static_sites`, removes neighbouring-court contacts with no re-track.
- *Experiment:* filter behind a flag; measure on a labelled multi-court video; on
  single-court fixtures show bit-identical rows.
- *Metric:* junk clip seconds on multi-court footage; zero change elsewhere.
- *Invariant risk:* low; "no polygon = no filter" by default.

### 4.3 Long bets

**L1 · Scale labels cheaply: one click per rally, a VLM as one weak vote, boundary review**
— L, videos, $

- *Hypothesis:* data is the binding constraint (D5). Labelling one point inside each rally
  reached 97% of full supervision on 50Salads *(excerpt)*; a review page that asks a person
  only about the least certain predicted boundaries (B-ACT-style) makes each labelled
  minute go further. A VLM (CF-594's labeller, PR #648) is one more vote on clip-level
  "in play?" questions, never a boundary.
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

**L2 · Learned highlight ranker** — M, after M5 and **≥ 5 highlight-labelled games**

- *Hypothesis:* a pairwise ranker over (cheer, rally shape, M1 tags, optional rally-window
  embedding) ranks better than the hand weights. Pairs multiply: 35 rallies give about 600
  pairs per game.
- *Gate:* at least five highlight-labelled games across two resolutions. With test1 plus
  CF-375 only, leave-one-game-out is train-on-one, test-on-one across 360p and 1080p —
  the "learns the venue" failure of CF-173.
- *Experiment:* follow the `dead_time_ml` pattern — committed JSON weights, numpy-only
  inference, `validated` flag, a `highlight_scorer` setting that falls back to the
  heuristic. Leave-one-game-out over highlight fixtures; harness Score AUC.
- *Invariant risk:* low as ranking-only; **medium if it moves the gate**, which decides
  which rallies get pose and clips. Ship ranking-only first.

**L3 · A small spotter over ball and pose tracks for serve and rally end** — L, videos, $

- *Hypothesis:* a temporal model over ball keypoints plus pose skeletons (UMEG-Net's idea,
  reimplemented — its repo has no licence) spots serves and rally ends better than
  contacts + pads, and catches short rallies whose only clear event is the serve.
- *Rule that keeps the invariant:* a spotted serve may open a rally only where the ball
  track has in-play samples on our court in the following seconds — *samples*, not
  contacts, so an ace with one detected contact still counts, and a neighbouring court's
  serve with none of our samples does not.
- *Cost note:* full-video pose was measured at about 15.6 GPU-min per game-hour (CF-198).
  Run pose at low resolution on candidate windows plus the uncertain gaps, not the whole
  video. CF-599 (#645, pose fallback for sparse tracks) is the related card.
- *Metric:* CF-593 serve-start error and short-rally recall; as a dead-time feature, the
  standard protocol.

**L4 · Pixel-level spotting spike** — L, videos, $ — existing card CF-597 (#643)

T-DEED (GPL-3.0) or AdaSpot (MIT) trained from ImageNet on serve frames. Expect it to
underperform: every SoccerNet BAS winner pretrained on 500 games, and no public volleyball
checkpoint can ship. Worth one spike only after L1 has produced 20+ games; a clear "no" is
an acceptable outcome.

**L5 · VLM as highlight judge** — M, videos, $ — existing card CF-600 (#646)

Fits "which of 35 rallies is best" better than the boundary question. Judge three key
frames or a short clip per already-accepted rally; never move a boundary. Compare against
M1 and L2 on the same fixtures before paying per match.

**L6 · Learned refinement stage (MS-TCN-style, reimplemented)** — L, cached, after 20+ games

Only if Q8 shows decoding matters. The official MS-TCN code is MIT + Commons Clause and
cannot be copied; ASRF (MIT) is the usable reference.

### 4.4 Ranking at a glance

| Rank | Item | Expected gain | Effort | Needs | Main risk |
|---|---|---|---|---|---|
| 1 | Q1 guarded ∪ learned | Derived: live cut 180 → 102 s, 26 → 16 whole rallies | S | caches | Selection bias inherited; must confirm |
| 2 | Q2 nested selection + per-rally | Honest verdict; reference row | S | caches | — |
| 3 | Q3 v3 vs v5 (CF-586) | Half the live cut at equal net (switch on) | S | caches | Tuned on 2 of 3 games |
| 4 | Q4 static-site filter for dead time | test4-type failures | S | caches | Moves both paths |
| 5 | Q5 soft-scale exponent (CF-376) | Past the 3-contact cliff | S | caches, CF-593 | Moves both paths |
| 6 | Q6 highlight re-baseline (CF-97) | Unblocks highlight decisions | S | test1 cache; videos for b | — |
| 7 | Q7 zero-clip paths (CF-294, CF-230) | Removes silent failures | S | — | — |
| 8 | Q8 duration-aware decoder | Fewer fragments | S–M | caches | May repeat CF-396 |
| 9 | M1 tagger cheer | Ranking (H5) | M | videos, Modal | Still court-blind |
| 10 | M2 clip ends + whistle (CF-38, CF-598) | Fewer butchered clips | M | caches, then videos | Neighbouring whistles |
| 11 | M4 tracker A/B (CF-596, CF-175) | Contacts, cost | M | videos | Cache bump if adopted |
| 12 | M3 frozen embeddings | test4-type failures | M | videos, small $ | Overfit; vs pose anchor |
| 13 | M5 persist features | Ranker's data | M | migration | Privacy review |
| 14 | M6 court filter | Multi-court junk | M | multi-court labels | Wrong polygon |
| 15 | L1 cheap labelling | More games — the real fix | L | videos, $ | Label noise |
| 16 | L2 learned ranker | Ranking | M | M5, ≥ 5 highlight games | Gate movement |
| 17 | L3 ball+pose spotter | Short-rally recall | L | videos, $ | Pose cost |
| 18 | L4 / L5 / L6 | Research answers | L / M / L | videos, $ / 20+ games | Low expected gain |

L1 is ranked low by expected *near-term* gain per unit of effort, not by importance: it is
the only item that fixes the binding constraint, which is why §5 starts it in Wave 0.

---

## 5. Recommended sequence

**Wave 0 — unblock (people; start now).** CF-595 (videos), D-0036 (caches), CF-593
(matcher), CF-375 (1080p highlight fixture), CF-398 (two new games). Ask a second person
to re-label one existing game (L1's noise measurement). Labelling is the long pole, so L1
starts here and runs throughout.

**Wave 1 — measure honestly on cached tracks (about 1–2 weeks, no spend).** Q2 first (it
defines the protocol), then Q1, Q3, Q4 and Q8 against Q2's reference row; Q5 as soon as
CF-593 lands; Q6a and Q7 in parallel. No defaults change. Output: a decision memo for a
person on `condense_mode` (union, v3 or guarded) at the production switch position.

**Wave 2 — add signals through existing seams (videos; 2–4 weeks).** M5 first, because it
costs little and every week without it is lost data. Q6b once videos are back. Then M1 and
M2 in parallel (different seams: `cheer_score` vs the highlight window end), M4's two free
arms, and M3 with its pose-anchor comparator. M4's paid arm (c) and M3's Modal step wait
for a person's approval.

**Wave 3 — learn from more data.** L2 once five highlight-labelled games exist. L3 once
there are about 8–10 comparable dead-time games. L4, L5 and L6 only after 20+.

Decisions reserved for a person: adopting any new weights (licence row in
`docs/licensing.md`), any paid re-track or Modal spend, flipping `condense_mode` or any
other default, and sending user video to a third-party API.

---

## 6. Proposed board cards

Titles follow the `CF-## · Description` style; numbers are left as `CF-??` because this PR
creates no issues (next free id at the time of writing: CF-602). Items that belong on an
existing card are listed second, as additions to that card rather than new cards.

**New cards**

| Proposed title | Plan item | Area | Depends on | Size |
|---|---|---|---|---|
| `CF-?? · Nested model selection, per-rally metrics and McNemar reporting in train_deadtime` | Q2 | eval, dead-time | caches | S |
| `CF-?? · validate_learned accepts every decoder dead_time_ml can emit` | Q2 | eval, dead-time | — | S |
| `CF-?? · Confirm guarded ∪ learned keep-windows held out at both switch positions` | Q1 | dead-time, eval | the nested-selection card | S |
| `CF-?? · Score the static-site filter on the comparable three for guarded, v3 and learned` | Q4 | dead-time, ball-detection | caches | S |
| `CF-?? · Explicit-duration Viterbi decoder with calibrated emissions` | Q8 | dead-time | the nested-selection card | S–M |
| `CF-?? · Label-noise estimate: second labeller and ±1 s jitter on the dead-time fixtures` | L1 | eval | a second labeller (human) | S |
| `CF-?? · Tagger-based cheer score and audio reliability flag on Modal` | M1 | audio, scoring | CF-595 | M |
| `CF-?? · Per-second SigLIP 2 / PE-Core embeddings as a dead-time veto/boost (eval-only)` | M3 | dead-time | CF-595, the nested-selection card | M |
| `CF-?? · Persist per-rally features, scores and gate decisions; record user clip actions` | M5 | scoring, api | privacy review, migration slot | M |
| `CF-?? · Court polygon filter on cached ball positions (default off)` | M6 | ball-detection | a multi-court labelled fixture | M |
| `CF-?? · One-click-per-rally labelling and boundary review page` | L1 | eval, web | CF-595 | L |
| `CF-?? · Learned pairwise highlight ranker behind a default-off scorer setting` | L2 | scoring | persistence card, ≥ 5 highlight fixtures | M |
| `CF-?? · Ball + pose keypoint spotter for serve and rally end (research)` | L3 | ball-detection, dead-time | CF-595, more labelled games | L |

**Additions to existing cards**

| Card | Addition from this plan |
|---|---|
| CF-586 (#611) | Q3: decide v3 vs v5 with per-rally metrics; add the union column. |
| CF-376 (#476) | Q5: pick the soft-scale exponent with CF-593, scoring both paths. |
| CF-97 (#97) | Q6: re-measure pre-gate from the cache now; post-gate and AUC after CF-595. |
| CF-2 (#26) | Q6b: the 0.50 vs 0.55 sweep comes free with the post-gate re-measure. |
| CF-294 (#344), CF-230 (#237) | Q7: renormalise on missing/flat audio; zero-contact runs trigger the fallback. |
| CF-38 (#47), CF-598 (#644) | M2: track-after-last-contact end, whistle as confirmation only, pad after the refined end. |
| CF-596 (#642), CF-175 (#181) | M4: VballNet arms (free, local) plus the 10 looks/s rate arm; read guarded columns per CF-416 (#547). |
| CF-397 (#518), CF-198 (#198) | M3's baseline (frame differences) and comparator (low-resolution pose anchor). |
| CF-594 (#640, PR #648) | L1's VLM vote; clip-level questions only. |
| CF-597 (#643), CF-600 (#646) | L4 and L5 as written. |
| CF-599 (#645) | L3's pose-on-sparse-tracks precursor. |
| CF-29 (#45, closed) | M6 should cite or reopen it: closed as completed, but nothing is on `main`. |

Prerequisites relied on throughout: CF-595 (#641), CF-593 (#639), CF-375 (#475), CF-398
(#519), CF-399 (#520), and the cost and licence cards CF-369 (#457) and CF-374 (#474).

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
