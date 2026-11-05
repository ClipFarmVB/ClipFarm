# Public volleyball datasets and pretrained models (CF-601)

**Status: survey complete; nothing adopted.** The card asks what public
volleyball data and models could replace or add to our own labels — ball
tracking sets, rally / action / group-activity sets, play-by-play with
timestamps, and pretrained volleyball models — and for each, what it is, how
big it is, what it labels, what licence it carries (compatible with AGPL and
a later commercial use?), and how it maps onto our needs. Everything below
was surveyed on 2026-11-05 by opening each cited page; nothing was
downloaded, no API was called, no model was run. Licences move and pages go
away, so the page's age matters: re-read a source before acting on a row,
and treat the date in each licence cell as part of the claim.

One limit shaped this survey and is stated up front rather than buried:
**every `universe.roboflow.com` page returned HTTP 403 to the fetcher this
survey could use**, and the repository, the Roboflow blog and the search
index do not name the workspace that owns `volleyball-ball-tracking-0eo7r`.
So the Universe licences here are what a search index showed of each page,
marked unconfirmed, and the `docs/licensing.md` UNKNOWN row stays open until
someone reads the model's License field — in a browser, or through the keyed
`api.roboflow.com` route `docs/licensing.md:153` records (see "What we would
do first").

## What "maps onto our needs" means

Every row's last column is judged against the same four needs, in the
project's own terms, plus whether the item could be scored on the fixtures
we already have.

| Need | What it is here | Where it is measured today |
|---|---|---|
| **Rally boundaries** | Serve contact to point end, derived from ballistic residuals on the ball track (`ml/pipeline/ball.py`, `README.md` Key Concepts). The dead-time fixtures label exactly this unit: 134 human spans across `ml/eval/fixtures/test2–5_deadtime.json` (`ml/eval/fixtures/README_deadtime.md`). | `ml/eval/harness.py --mode deadtime`; per-rally recall / precision / boundary error once CF-593's matcher lands. |
| **Contacts** | Individual ball touches, found as deviations from predicted free flight (`find_contacts`). No fixture labels contacts directly; they are observed through the rallies they produce (`ml/eval/tune_contacts.py`, `contact_cliff.py`). | Indirectly, via rally counts and the dead-time signals. |
| **Action labels (CF-3)** | `spike \| serve \| dig \| set \| block \| unknown` per rally, from trajectory heuristics overridden by YOLOv8-pose skeleton rules. | Not measured by any fixture; `test1.json` carries free-text notes ("spike") only. |
| **Highlight-worthiness** | `highlight_score` from crowd cheer plus rally shape; the `M` / `C` tiers of `ml/eval/fixtures/test1.json`. | `ml/eval/harness.py` default mode: Captured %, play buckets, incorrect time, Score AUC (`ml/eval/README.md`, "The four signals"). |

A dataset "maps onto" a need when its labels are the same unit (a serve
frame is a rally start; a per-touch event is a contact; a per-rally action
class is a CF-3 label), or when it would let us train or fine-tune a
component whose output we can then score on our fixtures through the
harness. A dataset that labels something adjacent (whole-clip "playing
volleyball", player boxes, group formations) is noted as such and ranked
lower.

One negative result up front: **nothing surveyed labels highlight-worthiness.**
No row maps onto the fourth need; the closest proxies are point-end markers
(VNL-STES `Score`, the Volleyball Dataset's and RIT-18's `winpoint`), which
say a rally ended, not that it was worth watching. CF-600 gets no public
ground truth from this survey.

The deep dives belong to the sibling cards and this survey hands them
pointers rather than doing their work: CF-596 (self-hosted ball tracker),
CF-597 (rallies from video), CF-598 (audio events), CF-599 (pose fallback),
CF-600 (highlight scoring). The licence posture it reads against is
`docs/licensing.md`.

## How to read the licence column

Four buckets, each with a verdict for today (the repo is AGPL-3.0-or-later)
and for a later commercial path (`docs/licensing.md`, "Exit path"), plus one
rule for the cells this survey could not settle.

| Bucket | Examples | Under AGPL today | Later commercial use |
|---|---|---|---|
| **Permissive** | MIT, Apache-2.0, BSD, CC0, CC BY 4.0 | Fine. Attribution where the licence asks for it. | Fine. CC BY needs a credit line in the product; nothing else. |
| **Copyleft** | GPL-3.0 / GPL-2.0-or-later (GPL-2.0-only is not AGPLv3-compatible), AGPL (ultralytics, and any YOLOv8-trained weights under Ultralytics' stated position) | Fine — it is the licence we already carry. | Constrains the exit path exactly as ultralytics does today: replace, or buy a commercial licence. One more copyleft weight makes the exit path longer, not different. |
| **Non-commercial** | CC BY-NC, CC BY-NC-SA, CC BY-NC-ND, research-only terms, academic EULAs | Usable for evaluation and research only, pending open question 3 — scoring against it, reading it, writing numbers from it. Not for training anything that ships, and not as fixture data in a product. | Blocks a commercial path, and **taints weights trained on it**: a model fine-tuned on CC BY-NC frames inherits the restriction whatever licence the code carries. |
| **Unstated** | A page with a citation request and no licence; a Google Drive zip; a GitHub repo with no LICENSE file | Treated as **all rights reserved**: readable, usable for nothing without asking. Several academic sets below are in this bucket, and "everyone uses it" is not a licence. | Same. An email to the authors is often all it takes, and the "Open questions" section names which ones are worth sending. |
| **Unread / unconfirmed** | A cell reading `Unconfirmed` (page 403, search snippet only), `Could not read`, `Unverified`, `Unknown`, or `Conflicting` (two of the item's own pages disagree) | Treated as **Unstated** until the page is read: a snippet is a lead, not a licence. For `Conflicting`, the stricter of the two readings governs until the author says which applies. | Same. Reading the page is the action, and it is listed under "What we would do first" where it matters. |

Three rules this survey applies, which `docs/licensing.md` already states
for the ball weights:

- **Data, code and weights are licensed separately.** A repo's MIT covers
  its code; the dataset it trains on and the checkpoints it publishes carry
  their own terms, which may be unstated even when the code's are clear.
  Where the three differ, the table gives separate cells or says which the
  cell refers to.
- **Nothing is inferred** from a venue, a GitHub star count, or "it is
  publicly downloadable". A licence appears in a cell only if a cited page
  prints it; otherwise the cell says `Unstated` and names the pages checked.
- **YouTube-sourced datasets carry a second layer**: even a stated licence on
  the annotations does not license the footage, which belongs to whoever
  uploaded it. Kinetics, Sports-1M, the Volleyball Dataset and RIT-18 are
  all in this position.

**Not legal advice.** This is an engineer's reading of licence pages, written
so the position is explicit and checkable. Adopting any dataset, model or
dependency named here is Nelson's decision (the always-a-person list), and
the non-commercial and unstated rows in particular would warrant a real
opinion before taking money.

## Ball-tracking datasets and trackers

| Item | What it is | Size | Labels | Licence (source) | AGPL / commercial | Maps onto our needs |
|---|---|---|---|---|---|---|
| **WASB-SBDT** (Tarashima et al., BMVC 2023) | A multi-sport small-ball detection and tracking baseline from NTT: one heatmap architecture trained per sport on five public datasets, with six re-implemented baselines (DeepBall, DeepBall-Large, BallSeg, TrackNetV2, ResTrackNetV2, MonoTrack) for comparison. The volleyball split is the Ibrahim et al. clips with the ball annotations of Perez et al. (2022). | Volleyball split: 3,493 train clips / 143,213 frames, 1,337 test clips / 54,817 frames; 4,830 clips in all, 178 (3.7%) with no visible ball (paper §4.1). | Per-frame ball (x, y) and visibility, in the TrackNet CSV convention. | Code: `MIT License`, `Copyright (c) 2023 NTT Communications Corporation` (`https://raw.githubusercontent.com/nttcom/WASB-SBDT/main/LICENSE.md`, read 2026-11-05). Weights: no separate statement in `MODEL_ZOO.md`. Volleyball data: redistributed as two Google Drive zips from `GET_STARTED.md`; its terms are the Volleyball Dataset's — **Unstated** (next section). Paper: `https://arxiv.org/abs/2311.05237`. | Code permissive. Volleyball weights are trained on unstated-licence YouTube frames: fine to evaluate, not fine to ship on a commercial path without clearing the data. | **Ball tracking, directly.** The only public tracker with volleyball weights and ~198k volleyball ball labels. The natural CF-596 candidate: run its volleyball WASB model over the fixture videos, build a `TrackedBall` from the per-frame positions (`BallPosition(frame, time, x, y, confidence)`, as `ml/eval/deadtime_variants.py:93` does from a cached track), run `find_contacts(tracker, frame_height=...)` then `contacts_to_rallies(contacts, duration, frame_height)` (`ml/pipeline/ball.py:1042`, `:1340`; the seam `ml/eval/harness.py:478-493` runs, which also applies `filter_static_sites` between the cached track and `find_contacts` and reads `normalize` / `soft_scale` off `settings` — at the defaults `ball_static_site_filter_enabled = False`, `ball_contact_scale_enabled = True`, `ball_contact_soft_scale_enabled = False` (`api/app/config.py:798`, `:766`, `:785`) that is the same measurement as the bare recipe), and score the rallies with CF-593's matcher against test2–5. Not `detect_rallies`, which runs the Roboflow detector itself and takes no external track (`ball.py:1465`). The experimental design beyond that seam is CF-596's. Caveat: the frames are broadcast-angle clips of 41 frames, not full gym-camera games. |
| **TrackNetV3** | PyTorch shuttlecock tracker for badminton: a TrackNet heatmap predictor with background estimation and mixup, plus an InpaintNet that fills occluded gaps in the trajectory. | Trained on the Shuttlecock Trajectory Dataset; size not stated on the repo page (the WASB paper counts the TrackNetV2 set at 172 + 29 clips, 91,214 frames). | Per-frame shuttlecock (x, y, visibility). | `MIT License`, `Copyright (c) 2024 qaz812345` (`https://raw.githubusercontent.com/qaz812345/TrackNetV3/master/LICENSE`, read 2026-11-05); the README states code and pretrained checkpoints are MIT (`https://github.com/qaz812345/TrackNetV3`). The badminton training data carries no licence (see TrackNetV2 row). | Code and checkpoints permissive. The checkpoints were trained on unstated-licence badminton frames, which matters only if the badminton weights themselves ship; a fine-tune on our own labelled frames would not carry them. | **Ball tracking, as an architecture to fine-tune.** Sport mismatch (shuttlecock, fixed broadcast camera), but the beach fork below shows it transfers on ~18k frames. The inpainting module is interesting for the detection gaps that currently split our tracks: `_segment_track` breaks a segment at a gap over `MAX_SAMPLE_GAP_SEC = 1.0` s (`ml/pipeline/ball.py:970`, `:222`, `:993`), after the tracker's own reset on `MAX_MISS` missed samples (`:45`, `:527`), an earlier and looser gate. Hand to CF-596. |
| **Beach Volley Vision** (ddecks) | A hobby project that fine-tuned TrackNetV3 on hand-labelled beach volleyball rally clips, then builds rallies, player tracks (RF-DETR + ByteTrack) and scoring on top; README says rally boundaries and the eval harness are still in progress. | Ball detector "trained on ~18,000 visible ball frames across 12 clips"; the labelled clips are not described as released. | Per-frame ball (x, y, visibility). | Repo `MIT license` (`https://github.com/ddecks/beach-volley-vision`); weights card `License: mit` (`https://huggingface.co/deadfast/beach-volley-vision-models`); both read 2026-11-05. Training footage provenance not stated. | Permissive on its face; the footage behind the weights is unstated. | **Evidence, more than data:** proves a TrackNetV3 fine-tune reaches usable volleyball ball tracking from a small labelled set — roughly five to ten of our fixture games' worth of sampled frames. test2–5 run 645.25 / 627.96 / 1,223.08 / 580.44 s (`video_duration_sec` in `ml/eval/fixtures/test2_deadtime.json` through `test5_deadtime.json`), which at the pipeline's `sample_every = max(1, round(_fps / 3.0))`, ~3 samples/s at any frame rate (`api/app/workers/tasks.py:1191`; both harness paths match it, `ml/eval/harness.py:473` and `:719`; `ARCHITECTURE.md:69`; `SAMPLE_EVERY = 3` at `ml/pipeline/ball.py:38` is the default `sample_every` of `track_ball` / `detect_contacts` / `detect_rallies` (`ball.py:497`, `:1409`, `:1468`), which neither production nor the harness uses, and the denominator that scales `max_jump` for every track (`ball.py:524`); it is not the production sampling rate) is 1.9k / 1.9k / 3.7k / 1.7k sampled frames per game (1,936 / 1,884 / 3,669 / 1,741; 9,230 together), dead time included; 18k visible-ball frames is therefore about 5 games (test4) to 10 games (test5) of total samples, roughly twice test2–5 together, and more games than that in visible-ball terms. Its weights are beach (sand, two players, outdoor light) and would need re-measuring indoors. The rally-boundary code it is building is the same problem as CF-597. |
| **TrackNetV2** (Sun et al., 2020) | The badminton successor to TrackNet: 3-frames-in, heatmap out, 31.8 fps. The reference implementation every later tracker (including WASB's baselines) re-implements. | 26 broadcast matches at 1280×720 30 fps: 23 professional (146 rallies, 68,675 frames) + 3 amateur (9,525 frames); 3 test matches, 29 rallies, 12,658 frames (`https://hackmd.io/Nf8Rh1NrSrqNUzmO0sQKZw`). The WASB paper counts the same set at 172 + 29 clips, 91,214 frames (TrackNetV3 row): the two sources differ by ~350 frames and count rallies and clips differently; each is quoted as its source prints it. | Per-frame Frame / Visibility / X / Y CSV per rally; rally MP4s included. | **Unstated.** No licence on the GitLab project page (`https://gitlab.nol.cs.nycu.edu.tw/open-source/TrackNetv2`), its README (`.../-/raw/master/README.md`), or the dataset description page; all read 2026-11-05. | All rights reserved until asked. | Architecture reference only; badminton footage. The labelling tool and per-rally CSV layout are a reasonable template for a ball fixture of our own. |
| **TrackNet** (Huang et al., 2019) | The original: tennis ball heatmap tracker from broadcast video, 640×360 input. | WASB counts the tennis set at 65 train clips (7 games, 14,160 frames) + 30 test clips (3 games, 5,675 frames). | Per-frame ball (x, y). | **Unstated.** No licence on the project page (`https://gitlab.nol.cs.nycu.edu.tw/open-source/TrackNet`) or README (`.../-/raw/master/README.md`); the README asks for citation only. Read 2026-11-05. | All rights reserved until asked. | Historical; superseded by V2/V3 and WASB. Set aside for practical purposes but listed because the card names it. |
| **SportsMOT** (ICCV 2023) | Multi-object *player* tracking in basketball, football and volleyball broadcast clips; players only, no ball. | 240 clips, >150K frames, >1.6M boxes (`https://arxiv.org/abs/2304.05170`); volleyball clips average ~360 frames; per-sport clip counts not on the pages read. | Player boxes with track IDs, MOT Challenge format. | `SportsMOT is licensed under a Creative Commons Attribution-NonCommercial 4.0 International License`; `You agree not to distribute the SportsMOT dataset without prior written permission` (`https://github.com/MCG-NJU/SportsMOT`, read 2026-11-05). Access via Codalab sign-up. | **Non-commercial.** Eval and research only; anything trained on it is tainted for a commercial path. | Weak. Player tracks could sharpen contacts (ball near a player's hands) or the CF-3 labeller's "who touched it", but we have no player-tracking stage and this would add one. Listed because the card names it. |

### Roboflow Universe volleyball projects (pages not readable from here)

Universe is where our in-use ball model lives and where the largest
volleyball ball/player image sets sit, so it matters most and is the least
verified. Every `universe.roboflow.com` URL below answered **HTTP 403** on
2026-11-05, on the project page and on `/model/1` and `/dataset/<n>` paths;
the "Licence" cell is therefore what the search index showed of that page on
the same day, and is marked **unconfirmed**. None of these is a claim this
survey can stand behind until the page is opened in a browser.

| Item | What it is | Size | Labels | Licence (source) | AGPL / commercial | Maps onto our needs |
|---|---|---|---|---|---|---|
| **`volleyball-ball-tracking-0eo7r/3`** — the model in use (`ml/pipeline/ball.py:37`) | The RF-DETR ball detector the pipeline calls through Roboflow `inference` (`ml/modal_app.py`). | Unknown. | `ball`, presumably; not read. | **Could not read.** The slug appears nowhere in the search index (`"0eo7r"`, extended search, 2026-11-05), the repo holds no workspace name, and a Universe URL needs `<workspace>/<project>`. `docs/licensing.md` row stays **UNKNOWN**. | Unknown — and this is the one row that sets clip boundaries, so it is not replaceable at the margin. | Already in use. Closing its licence is the first action below. |
| `egamberganov/volleyball-analysis-4ib8q` (→ model `volleyball-analysis-4ib8q-qv5e7/1`) | The dataset Roboflow's own May 2026 tutorial forks to train an RF-DETR ball detector and run it with ByteTrack (`https://roboflow.com/blog/automate-volleyball-tracking`, read 2026-11-05). | Not stated on the post. | Ball (the post reports 97.8% recall). | **Unconfirmed**: page 403; not in search snippets either. | Unknown. | A second RF-DETR ball model built the way ours was; a cheap A/B for CF-596 if its licence reads clean. |
| `shukur-sabzaliev-bh7pq/volleyball-tracking`, `shukur-sabzaliev1/volleyball_v2` and `volleyvision/volleyball-tracking/dataset/9` (VolleyVision) | The VolleyVision ball dataset. The README's main ball link is `shukur-sabzaliev-bh7pq/volleyball-tracking`; its dataset list gives `volleyvision/volleyball-tracking/dataset/9` as the 25k source (struck through) and `shukur-sabzaliev1/volleyball_v2` as the 19k de-duplicated version (`https://raw.githubusercontent.com/shukkkur/VolleyVision/main/README.md`, read 2026-11-05). | 19k / 25k images. | 1 class, `volleyball`. | **Unconfirmed**: pages 403. A search snippet of `shukur-sabzaliev1/volleyball_v2` shows `CC BY 4.0` (2026-11-05); none seen for the other two. The README that points at them carries the repo's own licence conflict (see Pretrained models). | Unknown; if it follows the repo README's CC BY-NC-ND, non-commercial. | Largest volleyball ball image set found; still-image boxes, so it trains a detector, not a tracker. CF-596 candidate once licensed. |
| `shukur-sabzaliev-42xvj/volleyball-actions` (VolleyVision) | Action boxes on still frames. | 14k images. | `block`, `defense`, `serve`, `set`, `spike`. | **Unconfirmed**: page 403. | Unknown. | The class set is CF-3's five, with `dig` named `defense`, per-image rather than per-rally — a CF-3 label-quality eval at the frame level, once licensed. |
| `shukur-sabzaliev-zc3en/volleyball-activity-dataset` and `activity-graz-uni/volleyball-activity-dataset` | Re-uploads of the TU Graz Volleyball Activity Dataset 2014 (next section) as Universe projects; the VolleyVision README says Roboflow split it randomly and recommends re-splitting sequentially. | As TU Graz: 18,960 frames. | `defense-move`, `attack`, `block`, `reception`, `service`, `setting`, `stand`. | **Unconfirmed**: pages 403. Whatever the Universe field says, the underlying data is TU Graz's and its terms are unstated. | Unknown; a re-upload cannot grant more than the original. | As the TU Graz row. |
| `studioprojektowe/volleyball-object-tracking` | A volleyball ball/player project. | Not read. | Not read. | Search snippet: `CC BY 4.0` (**unconfirmed**; page 403). | Permissive if confirmed. | Ball detector training data; CF-596. |
| `anaslab21/volleyball_track_ball` | Ball detection project. | Not read. | Not read. | Search snippet: `CC BY 4.0` (**unconfirmed**; page 403). | Permissive if confirmed. | Same. |
| `bert-masschaele-toxf0/volleyball-tracking`, `test-rnrvz/volleyball-ball-tracker`, `test-rnrvz/volleyball-tracker-4ctjb`, `volleyball-ug8ee/volleyballtracker-wvjp5`, `neurone/ball-detection-1bjgl`, `kamil-kaczmarek-txftt/volleyball-detection-test`, `volleyball-ai-detection/ai-volley-detection`, `qc/volleyball-hwxp2` | The other volleyball ball/player projects the search index surfaced. | Not read. | Ball and/or player boxes. | Snippets show `CC BY 4.0` for neurone, kamil-kaczmarek, volleyball-ai-detection and qc; none visible for the rest (**all unconfirmed**; pages 403). | Permissive if confirmed. | Pool for CF-596; each page's own License field has to be read, since a dataset's licence covers its images only and differs project to project. |

Roboflow's own guidance on what the field means is a community thread, not
a doc page: a forum member (`Jacob_Witt`; no role or badge visible to the
fetcher) wrote that MIT-licensed Universe data and models "will be
able to use data and models in proprietary use-cases" and asked users to
"do the due diligence to ensure the license works for your specific use case
and commercial requirements"
(`https://discuss.roboflow.com/t/data-privacy-and-software-license-for-open-source-datasets-on-roboflow-universe/3636`,
read 2026-11-05). The licence list itself was an image this survey could not
read.

## Player, action and group-activity datasets

| Item | What it is | Size | Labels | Licence (source) | AGPL / commercial | Maps onto our needs |
|---|---|---|---|---|---|---|
| **VNL-STES** (Nguyen et al., CVPR Workshops 2025) | A volleyball event-spotting benchmark: full Volleyball Nations League matches cut into rally clips by court-keypoint detection, each event marked at a frame and a pixel position. Video "sourced with permission from Volleyball World". | 8 games (2022–23 VNL), 1,028 rally clips (811 / 102 / 115 train / val / test), 6,137 events, ~251,110 frames at 25 fps full HD; the public download is resized to 398×224, 13 GB. | `Serve` (1,071), `Receive` (1,558), `Set`, `Spike`, `Block`, `Score` (244), each with a frame index and (x, y). | **Unstated** for the data: no licence or terms on the project page (`https://hoangqnguyen.github.io/stes`) or the code README (`https://github.com/hoangqnguyen/spot`), both read 2026-11-05; the CVPRW abstract page returned 403. Code: `BSD-3`. The footage is Volleyball World's, used by the authors "with permission", which is the strongest hint that redistribution terms exist and are not ours. | Code permissive. Data all rights reserved until the authors say otherwise; the Volleyball World layer makes a commercial path unlikely without a separate agreement. | **The closest match to three of four needs.** Rally clips cut by the authors = rally boundaries (and `Serve` / `Score` frames are the boundary events inside each clip); every event is a timestamped contact; the six classes are CF-3's set (`dig` ≈ `Receive`) plus `Score`. Broadcast angle, not gym camera. The right eval for CF-597 and the CF-3 labeller, if its terms allow research use. |
| **Volleyball Dataset** (Ibrahim et al., CVPR 2016; extended) | The standard group-activity benchmark: YouTube volleyball videos, hand-picked frames each with a 41-frame window (20 before, 20 after), player boxes, per-player actions and a frame-level group activity. Later extensions added player tracklets and manual ball positions (Perez et al. 2022). | 55 videos, 4,830 annotated frames (3,493 train / 1,337 test), ~198k frames with ball labels through the extension. | 8 group activities (`right/left` × `set`, `spike`, `pass`, `winpoint`); 9 player actions (`waiting`, `setting`, `digging`, `falling`, `spiking`, `blocking`, `jumping`, `moving`, `standing`); player boxes; ball (x, y) in the extension. | Code: `BSD 2-Clause license`. Data: **Unstated** — the README asks only that two publications be cited; download is a Google Drive folder (`https://github.com/mostafa-saad/deep-activity-rec`, read 2026-11-05). Footage is YouTube. | Code permissive. Data all rights reserved until asked, with a YouTube layer underneath. | Action labels: `setting`, `digging`, `spiking`, `blocking` are four of CF-3's five (no `serve`), labelled per player per frame rather than per rally; a per-rally label can be derived from the group activity. `winpoint` marks point ends at the clip level, but clips are 1.6 s windows, not rallies, so this is not a rally-boundary set. Its ball labels are the WASB volleyball split. |
| **MultiSports** (ICCV 2021) | Fine-grained spatio-temporal action detection in four sports; every action is a tube (per-frame boxes over a time span) on a named player. | 28,514 train / 10,116 val tubes across aerobics, volleyball, football, basketball; test labels withheld; gated download on Hugging Face (`https://huggingface.co/datasets/MCG-NJU/MultiSports`). Volleyball alone: `7645` instances over `139k` boxes (paper Table 2), train:val `3549:1294` (Table 6; `https://arxiv.org/html/2105.07404`, read 2026-11-05). | 12 volleyball classes. The Hugging Face card's label mapping ("Data Fields") lists them in an `id` / `Class` table as IDs 21–32 (the `id: name` form here is this survey's rendering): `21: volleyball serve`, `22: volleyball block`, `23: volleyball first pass`, `24: volleyball defend`, `25: volleyball protect`, `26: volleyball second pass`, `27: volleyball adjust`, `28: volleyball save`, `29: volleyball second attack`, `30: volleyball spike`, `31: volleyball dink`, `32: volleyball no offensive attack` (`https://huggingface.co/datasets/MCG-NJU/MultiSports`, read 2026-11-05) — these lowercase strings are what a downloaded label file carries. The paper's Appendix E.2 prints the same twelve, same order, as `Serve`, `Block`, `First Hit Pass`, `Defend`, `Protect`, `Second Hit Pass`, `Adjust`, `Save`, `Second Attack`, `Spike`, `Dink`, `No Offensive Attack` (`https://arxiv.org/html/2105.07404`); the GitHub README lists none. Key any eval on the card's strings, not the paper's. | `MultiSports Dataset License: CC BY_NC 4.0` (`https://github.com/MCG-NJU/MultiSports`); card tag `cc-by-nc-4.0` (Hugging Face); both read 2026-11-05. | **Non-commercial.** Eval only; trains nothing that ships. | Action labels with temporal extent, which is what CF-3's per-rally labels lack as ground truth: `volleyball serve`, `volleyball block`, `volleyball spike`, `volleyball first pass` / `volleyball defend` (≈ dig), `volleyball second pass` (≈ set), in the card's spelling, cover CF-3's set exactly. A CF-3 label-quality eval candidate, not training data. |
| **Volleyball Activity Dataset 2014** (TU Graz: Waltner, Mauthner, Bischof) | Professional Austrian Volley League 2011/12 games with per-frame player activity boxes. | 6 videos; `36178 annotations within 18960 frames`, 1920×1080 at 25 fps (search snippet of the TU Graz page). The abstract of `https://arxiv.org/abs/1404.6413` says `almost 36k annotated frames`, 7 classes, 6 videos — the page counts annotations and the abstract frames, so which unit 36k is stays open until the host page can be read. | `serve`, `reception`, `setting`, `attack`, `block`, `stand`, `defense/move`. | **Unstated**, and the host page could not be read: `https://tugraz.at/index.php?id=17751` reset the connection on three attempts on 2026-11-05; the search snippet of it shows a citation request only. Re-uploaded to Universe twice (previous section). | All rights reserved until asked. | Action labels: all five CF-3 classes are present (`attack` = spike, `reception` = dig), per frame. Small (six games) but gym-angle HD, which is closer to our footage than any broadcast set. A CF-3 eval candidate second to MultiSports on size, ahead of it on camera angle. |
| **RIT-18** (RIT ACTION Lab, CVPR Workshops 2020) | Group-activity clips from YouTube volleyball with compositional team-side labels. | 51 games, 1,530 clips, 12,035 annotated frames; Google Drive download. | 18 classes = team side × {`serve`, `firstpass`, `set`, `spike`, `volley`, `drop`, `shot`, `block`, `winpoint`}. | **Unstated**: the README asks for a citation and states no licence (`https://github.com/junwenchen/RIT-18`, read 2026-11-05). YouTube footage. | All rights reserved until asked. | Clip-level team actions including `serve` and `winpoint`; same shape as the Volleyball Dataset and smaller. CF-3 eval at best. |
| **GYdevy `volleyball_rally_no_rally`** (Hugging Face) | A rally-versus-no-rally set from an individual account; empty README, dataset viewer fails on its class labels. | 3.57 GB; no counts on the card. Sibling repos `rally_ds_v2` (4.19 GB, no card, no licence) and `volleyball-big-ds` (no card read) are the same account. | Two-class, by name. | Card field `apache-2.0` (`https://huggingface.co/datasets/GYdevy/volleyball_rally_no_rally`, read 2026-11-05); provenance of the clips not stated. | Permissive on the card; the footage's own terms are unknown. | In principle the exact in-play / dead-time unit the dead-time fixtures use; in practice unknown contents and unknown footage. Worth one download by CF-597 to see what is in it; not worth more until it has a README. |
| **GYdevy `volleyball-whistles`** (Hugging Face) | Full-length indoor match audio (22.05 kHz WAV) with manually labelled whistle times, raw and re-anchored to a band-peak feature. | 3.29 GB; match count not stated. | Per whistle: `match_id`, `whistle_id`, `global_id`, `time`, `type`, `t_raw`, `t_anchor` (the card's example record, read 2026-11-05). | Card field `mit` (`https://huggingface.co/datasets/GYdevy/volleyball-whistles`, read 2026-11-05); recording provenance not stated. | Permissive on the card. | Whistles bracket rallies (point end) and are the one audio event that is court-specific rather than crowd-wide — the `README.md` Key Concepts note that cheer is court-blind. A direct CF-598 pointer. |
| **VREN** (arXiv 2022) | A rally-notation dataset: professional and NCAA Division I rallies written in an expression language of player actions, positions and ball travel, for rally-outcome and set/hit-type prediction. | One file, `dataset_full.csv`; no counts on the repo page. | Per-rally action sequences in the notation; no timestamps, no video. | **Unstated**: no README, no LICENSE (`https://github.com/haotianxia/VREN`, read 2026-11-05); the arXiv page's CC BY-NC-ND badge is the paper's licence, not the data's (`https://arxiv.org/abs/2209.13846`). | All rights reserved until asked. | Play-by-play without video or time, so it maps onto nothing we measure. Listed here rather than set aside only because it is the one public per-rally action log for indoor volleyball. |

## Play-by-play and scouting data

| Item | What it is | Size | Labels | Licence (source) | AGPL / commercial | Maps onto our needs |
|---|---|---|---|---|---|---|
| **openvolley `datavolley`** (Ben Raymond et al.) | R package that parses DataVolley `.dvw` scout files (also ovscout2, Click & Scout, VolleyStation `.vsm`) into a per-touch table. `dv_sync_video()` aligns every scouted contact to video time from the first serve's video time, using the scouted clock times, and is marked experimental (`Warning: experimental!`; its manual page, `https://datavolley.openvolley.org/reference/dv_sync_video.html`, read 2026-11-05, takes `first_serve_contact`, "the video time of the first serve contact", and says "The clock time of each serve will be used as the reference time for each rally"); `ovva` reads the video path a `.dvw` stores in its `[3VIDEO]` section (`https://github.com/openvolley/ovva`, read 2026-11-05). | Package; ships example files via `dv_example_file()` (one is a 2015 Slovenian junior final). | Per touch: player, skill (serve, reception, set, attack, block, dig, freeball), evaluation code, zones, rally and set boundaries, clock time; video time once synced. | `MIT + file LICENSE` (`https://raw.githubusercontent.com/openvolley/datavolley/master/DESCRIPTION`); `ovva` and `ovdata` the same (`.../openvolley/ovva/master/DESCRIPTION`, `.../openvolley/ovdata/master/DESCRIPTION`); all read 2026-11-05. | Permissive. | **The unit is right:** a synced `.dvw` is a per-contact, per-rally, per-point log with video times — rally boundaries, contacts and CF-3 labels in one file — for whichever game it was scouted on. The limit is supply: a `.dvw` is useful to us only paired with its video, and public pairs are rare (the `ovdata` examples name contributors, not footage). The path this opens is *our* games scouted by a volunteer in ovscout2, not a public dataset. |
| **openvolley `ovdata`** | Example scout files for the openvolley packages, contributed by named coaches and `odbojka.si`. | A handful of files in `inst/extdata`; no video mentioned on the page. | As `.dvw`. | `MIT + file LICENSE` (source as above). | Permissive. | Format fixtures for a `.dvw` reader; no video, so no eval. |
| **FIVB VIS web service** | FIVB's live-data export: options include `RallyScore`, `RallyLineUp`, `EventsRallies` and `EventsTimeOffsets` — rally events with a time offset from match start. | Not a dataset; a service. | Per-rally score, line-up, events, time offset. | Access terms not on the documentation page (`https://www.fivb.org/VisSDK/VisWebService/VolleyLiveExportOption.html`, read 2026-11-05); nothing says it is public. **Unverified.** | Unknown. | Per-rally timing exists in the schema, which is the only match-level stats source where it does. Without public access and without video it is a note for later, not a candidate. |

Match-level stats sources (VolleyballWorld pages, Kaggle box-score sets,
BigTimeStats' AVP/FIVB beach results — the last one `Creative Commons
Attribution-NonCommercial-ShareAlike 4.0 International`, `Non-commercial use
only. Attribution required.`, `https://github.com/BigTimeStats/beach-volleyball`,
read 2026-11-05, and holding match totals and durations, not play-by-play)
are set aside below: none carries per-rally timing against a video.

## Pretrained models

| Item | What it is | Size | Labels | Licence (source) | AGPL / commercial | Maps onto our needs |
|---|---|---|---|---|---|---|
| **RF-DETR `volleyball-ball-tracking-0eo7r/3`** (in use) | The Universe RF-DETR ball model behind `ml/pipeline/ball.py`, loaded by Roboflow `inference==1.3.3` (Apache-2.0) on Modal; weights ~114 MB from Roboflow's CDN (`ml/modal_app.py`). | — | `ball`, presumably; not read (`ml/pipeline/ball.py` filters on no class name). | **Could not read** — see the Universe table. Two layers: the Universe project's License field (unread), and the RF-DETR base weights it was fine-tuned from: `Apache-2.0` for Nano/Small/Medium/Large detection, `Roboflow Platform Model License (PML 1.0)` for Atto/Femto/Pico/XL/2XL and the NAS models, where "Commercial use is included when you deploy on Roboflow" and commercial self-hosting needs the Enterprise add-on (`https://github.com/roboflow/rf-detr`, read 2026-11-05). Which size version 3 was trained from is not recorded in the repo. | Unknown on both layers; we deploy it through `inference`, not Roboflow's hosted platform, so the PML distinction would bite if the base is one of its sizes. | Already in use; sets clip boundaries. Both layers are the first action below. |
| **Roboflow blog RF-DETR + ByteTrack** (`volleyball-analysis-4ib8q-qv5e7/1`) | Roboflow's May 2026 tutorial model: RF-DETR trained on the forked `egamberganov` dataset, tracked with ByteTrack in a Workflow; 97.8% recall reported (`https://roboflow.com/blog/automate-volleyball-tracking`, read 2026-11-05). | — | `ball`. | Dataset page unread (403). Of the two layers above, the base is known here: the post picks `Roboflow RF-DETR Object Detection (Small)` and says `Because it's Apache 2.0 licensed, RF-DETR can be used in commercial projects without licensing fees` (`https://roboflow.com/blog/automate-volleyball-tracking`, read 2026-11-05); Small is in the rf-detr README's Apache-2.0 group. The dataset layer stays unread. | Base weights permissive; the dataset layer unknown. | A/B candidate for CF-596 if its dataset licence reads clean; same architecture as ours, so the comparison isolates the data. |
| **VolleyVision weights** (shukkkur) | Student diploma project: YOLOv7-tiny ball weights (74.1% mAP50) in `Stage I - Volleyball/yV7-tiny/weights`, YOLOv8m action and player weights (92.3% / 97.2% mAP50) in `Stage II - Players & Actions/actions/yV8_medium/`, a DaSiamRPN tracker, and Roboflow-hosted models for each stage plus court segmentation. | — | Ball; `block`, `defense`, `serve`, `set`, `spike`; `player`. | **Conflicting.** README: `This project is licensed under the Creative Commons Attribution-NonCommercial-NoDerivatives (CC BY-NC-ND) license.` (`https://raw.githubusercontent.com/shukkkur/VolleyVision/main/README.md`); `LICENSE.md`: `GNU AFFERO GENERAL PUBLIC LICENSE Version 3, 19 November 2007` (`https://raw.githubusercontent.com/shukkkur/VolleyVision/main/LICENSE.md`); both read 2026-11-05. The YOLOv8 weights also sit under Ultralytics' stated AGPL position on weights (`docs/licensing.md`). | Under the README's reading, **non-commercial and no derivatives**, which forbids fine-tuning; under the LICENSE file, AGPL like ultralytics. Either way not a commercial-path asset; the README reading forbids even the eval fine-tune. | Ball and action detectors on volleyball frames; the action classes are CF-3's. Useful as a comparison point for CF-596 (ball) and the CF-3 labeller (actions) only if the author resolves which licence applies. |
| **WASB volleyball weights** (7 models) | Volleyball checkpoints for WASB and six baselines, one Google Drive link each (`https://github.com/nttcom/WASB-SBDT/blob/main/MODEL_ZOO.md`, read 2026-11-05). | — | Ball heatmap. | Repo MIT; no separate statement for the weights; trained on the Volleyball Dataset frames (unstated). | Evaluate freely; shipping them on a commercial path inherits the data question. | CF-596's first self-hosted candidate: no training needed to get a number on the fixtures. |
| **TrackNetV3 checkpoints**; **Beach Volley Vision fine-tune** | `TrackNet_best.pt` / `InpaintNet_best.pt` (badminton); `tracknet_best.pt` (beach volleyball, ~130 MB; InpaintNet not retrained). | — | Ball heatmap. | MIT, both (rows above). | Permissive; badminton checkpoint carries unstated badminton frames, beach checkpoint unstated beach footage. | Starting points for a CF-596 fine-tune on our own labelled frames rather than models to run as-is. |
| **Pose alternatives** — YOLOv8-pose (in use), ViTPose, RTMPose, MediaPipe Pose | `docs/licensing.md` "Exit path" names two of these (`MediaPipe Pose, Apache-2.0; ViTPose, Apache-2.0`, `docs/licensing.md:94-95`); RTMPose is this survey's addition. Licences re-read 2026-11-05: ViTPose `Apache-2.0 license` (`https://github.com/ViTAE-Transformer/ViTPose`); RTMPose lives under `projects/rtmpose` in mmpose, whose root `LICENSE` reads `Apache License`, `Version 2.0, January 2004`, `Copyright 2018-2020 Open-MMLab. All rights reserved.` (`https://raw.githubusercontent.com/open-mmlab/mmpose/main/LICENSE`), and the project README carries no separate licence statement (`https://github.com/open-mmlab/mmpose/tree/main/projects/rtmpose`); MediaPipe `Apache-2.0 license` (`https://github.com/google-ai-edge/mediapipe`). YOLOv8-pose weights: AGPL per Ultralytics' position. | — | COCO-17 keypoints (ViTPose, RTMPose), 33 landmarks (MediaPipe). | As stated per item. | Three permissive replacements for the one AGPL dependency; mmpose's `LICENSES.md` for per-weight terms was not read. | CF-3 action labels only; not boundaries. All of this is CF-599's, which this survey only confirms. |
| **Audio event models** — YAMNet, PANNs; AudioSet | YAMNet: a MobileNet-v1 classifier over 521 AudioSet classes (`https://github.com/tensorflow/models/tree/master/research/audioset/yamnet`). PANNs: AudioSet-trained CNNs for tagging and sound-event detection (`https://github.com/qiuqiangkong/audioset_tagging_cnn`). AudioSet: the training corpus behind both. | — | AudioSet classes; whether a whistle class is in YAMNet's 521 was not confirmed (the class map was not read). | YAMNet's repo `Apache License, Version 2.0` (`https://raw.githubusercontent.com/tensorflow/models/master/LICENSE`); PANNs `MIT license` (repo page); AudioSet `Creative Commons Attribution 4.0 International (CC BY 4.0)`, ontology `CC BY-SA 4.0` (`https://research.google.com/audioset/download.html`). All read 2026-11-05. | Permissive throughout. | Whistle / cheer spotting for CF-598; the `volleyball-whistles` set above would be its eval. One-line pointer only. |

## Ranked top 3

**1. VNL-STES.** It is the only public set whose labels are the same units
we measure: rallies as clips, `Serve` and `Score` as frame-level boundary
events, every touch as a timestamped `Receive` / `Set` / `Spike` / `Block`
contact, and a class set that is CF-3's plus `Score`. On it we could score
CF-597's rally-from-video approaches and the CF-3 labeller against 6,137
hand-placed events across 1,028 rallies, nearly eight times the 134 spans
of the four comparable dead-time fixtures and the first ground truth for
contacts and action labels we would have at all. It is broadcast footage at
398×224, so it tests a method rather than our camera; and its data licence
is unstated with Volleyball World's footage underneath, so it is an eval set
pending an email, never training data for something that ships.

**2. WASB-SBDT with the Volleyball Dataset ball annotations.** ~198k
volleyball frames with ball positions, MIT code, and ready volleyball
checkpoints for seven architectures. It answers CF-596's question —
can a self-hosted small-ball tracker replace the Roboflow RF-DETR call? —
without training anything: run the volleyball WASB model over the fixture
videos (when CF-595 lands them), wrap the positions in a `TrackedBall` and
hand it to `find_contacts` then `contacts_to_rallies` — the seam the harness
already uses for cached tracks (`ml/eval/harness.py:478-493`), since
`detect_rallies` runs its own Roboflow call and takes no track — and read
rally recall, precision and boundary error off CF-593's matcher next to the
current model's. It maps onto rally boundaries and contacts through
the ball track exactly as the current model does. The data's licence is
unstated and the footage is YouTube, which does not stop the measurement
and does stop shipping its weights commercially without clearing it.

**3. MultiSports (volleyball split).** The largest fine-grained,
temporally-extended action labelling of volleyball in the open: `volleyball
serve`, `volleyball block`, `volleyball spike`, `volleyball first pass`,
`volleyball second pass`, `volleyball defend` and six more, as the Hugging
Face card spells them (the paper writes `First Hit Pass` / `Second Hit Pass`),
7,645 instances in all across train, val and test (paper Table 2), of which
4,843 (train 3,549 + val 1,294, Table 6) are downloadable with labels, the
test labels being withheld — as per-frame tubes on named players. CF-3's labels have no ground truth today;
this gives a per-rally eval of the trajectory heuristics and the pose
override, and separates "the label is wrong" from "the pose is wrong". It
is CC BY-NC 4.0, so it is an eval set and nothing else — which is all CF-3
needs from it. The TU Graz Volleyball Activity Dataset would take this slot
on camera angle (gym, HD) if its terms were ever stated; by annotation
count it is roughly a quarter of the size (36,178 annotations on the TU Graz
page against MultiSports' volleyball 139k boxes, paper Table 2 — approximate,
since the TU Graz abstract gives the same figure as frames and the 139k
includes the withheld test split) and it is
unreachable today.

Not in the top three, and why: the Roboflow Universe projects would rank
first for CF-596 training data if their licence fields could be read — they
are the only large volleyball ball image sets with (per the snippets)
permissive terms — and move up the moment one is opened in a browser.
`datavolley` is the right *format* for everything at once and the wrong
*supply*: it earns its place when we scout one of our own games, not from
any public file.

## What we would do first

Concrete, in order, and changing no default. Each is a separate small
follow-up, not this PR.

1. **Close the `docs/licensing.md` UNKNOWN row by reading the model's page
   in a browser.** Needs the workspace slug, which only the Roboflow account
   that owns the model shows (the project's Universe URL is
   `universe.roboflow.com/<workspace>/volleyball-ball-tracking-0eo7r`; the
   workspace is on the Roboflow dashboard, and the public page needs no
   key). `docs/licensing.md:153` records a second route,
   `api.roboflow.com/volleyball-ball-tracking-0eo7r?api_key=...`, which
   needs the key and no slug; the browser route is preferred here only
   because the research cards' rules forbid Roboflow calls, and either
   closes the row. Quote the License field verbatim into the audit row with the date,
   and record which RF-DETR size version 3 was trained from, because the
   base weights' licence (Apache-2.0 or PML 1.0) is a second layer the audit
   does not yet mention. If the field reads `CC BY-NC`, the exit path in
   `docs/licensing.md` gains a step ahead of the pose one, since ball
   tracking sets clip boundaries. **Nelson's**: only his account sees the
   workspace; the edit to `docs/licensing.md` is a one-line follow-up.

2. **Hand CF-596 the ball-tracker candidates with their licence reads:**
   WASB volleyball checkpoints (MIT code, unstated data) as the no-training
   baseline; TrackNetV3 (MIT) as the fine-tune architecture, with Beach
   Volley Vision as the proof it works on ~18k visible-ball frames — which is
   five to ten fixture games of sampled frames, not one (test2–5 give
   1.7k–3.7k samples each at the pipeline's ~3 samples/s, dead time included;
   the row above has the per-game numbers and the rate's source lines), so a
   labelling pass sized from it is five to ten games, about twice test2–5
   together (`test1_deadtime.json` alone runs 3,660 s, ~11k samples at the
   same rate, so against all five dead-time fixtures, ~20k samples, 18k is
   under one set); the Universe projects
   as training data *once a page is opened*. The measurement is the one in
   `ml/eval/README.md`: same fixtures, same matcher, current model beside
   each candidate.

3. **Ask the VNL-STES authors for research-use terms** (one email; the
   project page has none and the footage is Volleyball World's). If granted,
   CF-597 scores its rally-from-video methods on the 1,028 rally clips and
   the CF-3 labeller runs on the 6,137 events. If refused, nothing is lost
   and the row moves to "Looked at and set aside". **Nelson's** to send or
   not, as it is an external ask in the project's name.

4. **Keep MultiSports and the Volleyball Dataset as CF-3 label-quality
   evals, later.** Both need a download under terms that forbid anything
   but research (MultiSports) or state nothing (Volleyball Dataset: ask).
   Neither should sit in `ml/eval/fixtures/`, whose files are the project's
   own labels; a research eval would read them from a path outside the
   repo, following the convention the research cards set for unlabelled
   games (`CLIPFARM_EXTRA_VIDEO_DIR`, read by CF-594's `locate`; that branch
   is unmerged and nothing on `main` defines the variable yet).

5. **Point CF-598 at `volleyball-whistles` (MIT) and YAMNet / PANNs**, and
   CF-599 at the two Apache-2.0 pose models already in `docs/licensing.md`
   (MediaPipe Pose, ViTPose) plus RTMPose, read from mmpose above. Pointers
   only.

6. **Note `datavolley` as the path for our own labels, not public ones.**
   A volunteer scouting one fixture game in ovscout2 would produce every
   contact, rally and action with a video time in one file, under MIT
   tooling. That is a labelling decision (CF-56's protocol, the dead-time
   fixture format) and out of this card's scope.

Adopting any dataset, model or dependency above — downloading one into the
pipeline's reach, training on it, swapping the ball model, adding an R or
Python package — is **Nelson's decision**. This report recommends and
adopts nothing.

## Looked at and set aside

- **Kinetics (400/600/700), `playing volleyball` class** — ~10 s YouTube
  clips with one video-level label; no rally, contact or action unit. The
  licence commonly given (CC BY 4.0) appears on mirrors and third-party
  cards only; the official repo (`https://github.com/cvdfoundation/kinetics-dataset`),
  its README and the S3 readme (`https://s3.amazonaws.com/kinetics/400/readme.md`)
  print none (read 2026-11-05). Coarse and unverified.
- **Sports-1M** — 1,133,158 YouTube URLs with 487 auto-generated topic
  labels; `This data set is made available under a Creative Commons
  License:` CC BY 3.0 (`https://github.com/gtoderici/sports-1m-dataset`,
  read 2026-11-05). Video-level labels over footage the licence does not
  cover; no unit of ours.
- **UCF101 `VolleyballSpiking`** — could not verify: both
  `https://www.crcv.ucf.edu/data/UCF101.php` and
  `https://www.crcv.ucf.edu/research/data-sets/ucf101/` failed TLS
  verification from this environment on 2026-11-05. Clip-level action class
  in any case.
- **SoccerNet** — methodology pointer only (action spotting with
  per-event timestamps is the shape VNL-STES borrows). Its broadcast video is
  gated behind a Non-Disclosure Agreement via Hugging Face
  (`https://www.soccer-net.org/data`, read 2026-11-05) and it is football.
- **SportsMOT** — kept in the table because the card names it; set aside in
  practice: players only, CC BY-NC.
- **VREN** — kept in the table; no video, no time, no licence.
- **FIVB VIS web service** — rally time offsets exist in the schema; access
  terms not published; no video.
- **BigTimeStats beach-volleyball** — match totals and durations, no
  play-by-play, CC BY-NC-SA 4.0.
- **VolleyballWorld / Kaggle match stats** — box scores without per-rally
  timing; not opened individually.
- **`ryan1288/digdeep_data` (Hugging Face)** — card says raw games plus
  snippets of consecutive plays "including downtime", licence field
  `agpl-3.0`, but the repo is 4.34 kB and its viewer fails: no video is
  actually there (`https://huggingface.co/datasets/ryan1288/digdeep_data`,
  read 2026-11-05).
- **GYdevy `rally_ds_v2`, `volleyball-big-ds`** — no card, no licence, no
  README; the sibling with a licence field is in the table.
- **RacketVision, PadelTracker100** — ball-tracking sets surfaced by the
  search; racket sports and padel, no volleyball.
- **Gomez et al., "Tracking of Ball and Players in Beach Volleyball Videos"
  (PLoS ONE 2014)** — method paper; no dataset release found.
- **Paperswithcode / hyper.ai dataset mirrors** — third-party copies of the
  Volleyball Dataset; not a source, and a licence they print is not the
  authors'.
- **TrackNet (2019)** — kept in the table for completeness; superseded.

## Open questions

For Nelson, since each is a licence, an external ask, or an adoption:

1. What is the workspace slug of `volleyball-ball-tracking-0eo7r`, what does
   its public License field read, and which RF-DETR size was version 3
   trained from? (Closes the `docs/licensing.md` row; the second layer is
   new. The keyed `api.roboflow.com` route at `docs/licensing.md:153` is the
   alternative if the slug is not to hand.)
2. May the project email the VNL-STES authors for research-use terms, and
   the Volleyball Dataset authors for the same? Both sets are unstated;
   both are one message away from a yes or no.
3. Is a CC BY-NC eval set (MultiSports, SportsMOT) acceptable inside the
   proposed `ml/research/` (the directory the research cards' rules permit;
   not in the tree yet) at all, given a later commercial path, or should
   research evals be limited to unstated-but-asked and permissive sets?
   Non-commercial terms cover use, and an eval is a use.
4. VolleyVision's README and LICENSE file disagree (CC BY-NC-ND vs
   AGPL-3.0). Is it worth asking the author which applies, or is the
   project simply not a candidate under either?
5. Would a volunteer scout one fixture game in ovscout2 (`.dvw`, MIT
   tooling)? That is the only route in this survey to per-contact labels on
   our own camera angle, and it is a labelling decision rather than a
   dataset one.
