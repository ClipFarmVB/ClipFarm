# Licensing

**ClipFarm is licensed AGPL-3.0-or-later.** See `LICENSE` (verbatim FSF text).

    Copyright (C) 2026 Nelson Kang

This file is the written answer CF-178 asked for: *may we run this as a
commercial service given what we depend on?* The short version is that we did
not pick AGPL as a preference — a dependency picked it for us, and the honest
move was to say so rather than declare something we are not entitled to
declare.

Everything below was **measured on 2026-09-08**, not recalled. Where a fact
came from a file you can open, the path is given; where it came from package
metadata, the method is in [Audit method](#audit-method). Licenses move, so
re-measure rather than trusting this page's age.

---

## The decision

Before CF-178 the repo was **public with no `LICENSE`**, which is "all rights
reserved" by default: readable by anyone, legally usable by no one. That is a
real position and it was not a chosen one — the worst of the available
combinations, since it gives away nothing and protects nothing.

Three postures were coherent. We took the third:

| | Posture | Why not / why |
|---|---|---|
| 1 | Private + proprietary | Destroys the repo's present value — it is a public portfolio piece. And it does **not** resolve the obligation below: AGPL §13 runs to *users of the service*, not to the public, so a private repo owes source to anyone who uploads a video. |
| 2 | MIT / permissive | Not ours to grant. See below. |
| 3 | **AGPL-3.0-or-later** | States an obligation we already carry. Costs nothing, forecloses nothing that matters, and is the most fork-hostile of the open licenses — which is the right shape if the worry is someone lifting this into a competing service. |

## Why a permissive license was not available

We depend on **`ultralytics`, which is AGPL-3.0-or-later.**

- Pinned at `ultralytics==8.3.55` in two places: `ml/modal_pose.py:59` (the
  Modal pose image) and `ml/requirements.txt:2`. The second is a documented
  install path, not a dead pin — `README.md:330`, `DEPLOY.md:232` and
  `Dockerfile.api:55` all direct the reader to it, and
  `api/tests/test_pose_modal.py:973` asserts ultralytics is in it. An earlier
  version of this section said "installed in exactly one place", which was
  contradicted by a file the audit method above says it read.
- Imported at `ml/pipeline/detect.py:66`, `:148` and `:485` — all lazy, all
  behind guards that degrade rather than crash.
- Its pretrained weights (`yolov8s-pose.pt`, `yolov8n-pose.pt`, baked into the
  image at build time) are treated as AGPL by Ultralytics as well. That is
  *their stated position*, asserted here as their position rather than as
  settled law — but it is the position they sell Enterprise licenses against,
  and they are known to enforce it.

AGPL §13 (`LICENSE:540`, "Remote Network Interaction") extends GPL's
source-offer obligation to users who interact with the program **over a
network**. ClipFarm is a hosted service that users upload video to. That is
squarely the scenario §13 exists for.

The step worth stating plainly, because it is where the reasoning could go
wrong: §13 speaks of *modifying* the Program, and we do not patch ultralytics —
we import it. Under the FSF's reading (and Ultralytics' own), importing a
library into your program produces a combined work "based on the Program" under
GPLv3 §5, which is what makes our pipeline a modified version for §13's
purposes. A narrower reading of derivative-work scope exists in the abstract;
it is not the reading the copyright holder enforces, and this project is not
the place to test it.

**Licensing our own code MIT would not have removed an obligation a dependency
imposes** — the two are independent. Declaring "all rights reserved" over a
work that incorporates AGPL code would have been worse than the silence it
replaced: silence is ambiguous, an affirmative proprietary claim is a
violation on its face.

## What this does and does not commit us to

- **It does not bind us.** We hold the copyright on our own code. AGPL
  constrains everyone downstream, not the author. Relicensing later needs no
  one's permission.
- **What cannot be undone** is that the versions published under AGPL stay AGPL
  for anyone who took a copy. For a pre-launch tool with no users that is
  theoretical, and it is the whole of the downside.
- **The obligation is to service users, not the world.** §13 requires offering
  Corresponding Source to people interacting with the deployed instance.
  A public repo satisfies that comfortably.

## Exit path, if ClipFarm goes commercial

Two ways out, in the order they should be considered:

1. **Replace the pose model.** Bounded, and the code is already shaped for it:
   pose feeds *action labels* (dig/set/spike, CF-3), not clip boundaries —
   those come from ball tracking, which is RF-DETR and unaffected. The three
   `detect.py` import sites are already guarded for absence, so the seam
   exists. Candidates are permissively licensed (MediaPipe Pose, Apache-2.0;
   ViTPose, Apache-2.0). Needs its own card plus an eval pass on the action
   labels — the skeleton heuristics assume the COCO 17-keypoint layout
   (`ml/modal_pose.py:58`), so a model with a different layout is not a drop-in.
2. **Buy an Ultralytics Enterprise license.** Zero engineering, quote-based
   recurring cost. Rational once there is revenue; not before.

Either way, relicensing applies from that point forward, not retroactively.

---

## Dependency audit

### Audit method

- **Python:** license expression / `license` field / trove classifiers read
  from the PyPI JSON API (`https://pypi.org/pypi/<name>/json`) for every
  direct dependency in `api/requirements.txt` and `ml/requirements.txt`.
  `api/requirements-dev.txt` is not covered — it is test-only and never in the
  production image (pytest MIT, PyYAML MIT, numpy BSD, checked but not swept
  systematically).
- **npm:** the `license` field of the `package.json` of all **591** installed
  packages under `node_modules/` — the full transitive tree, not just direct
  deps.
- **Bundled binaries:** read out of the installed wheel itself, not inferred
  (see [FFmpeg](#ffmpeg-two-separate-questions)).

Transitive Python dependencies were **not** individually enumerated; the direct
set plus the copyleft sweep below is the coverage this audit claims. That is a
real limit, stated rather than papered over.

### Findings that need a decision

| Package | License | Where | Status |
|---|---|---|---|
| `ultralytics==8.3.55` | **AGPL-3.0-or-later** | `ml/modal_pose.py:59` | **Determines the repo's license.** See above. |
| `psycopg2-binary` | **LGPL-3.0 with exceptions** | `api/requirements.txt` | Fine, but for the LGPL's own reason rather than the exception's: the LGPL already permits linking from software under any license. psycopg2's "special exception" is specifically an **OpenSSL** exception — permission to distribute combinations linked against OpenSSL, whose licence is otherwise GPL-incompatible. It grants nothing about *our* code. What makes this fine is the LGPL plus the fact that we neither modify nor redistribute it. Second-most copyleft thing in the tree, so worth knowing it is here. |
| `@sentry/cli` (+ `-win32-x64`) | **FSL-1.1-MIT** | npm, transitive via `@sentry/nextjs` | Fine, but **not OSI open source** — Functional Source License, source-available, forbids competing with Sentry. Build-time only (source-map upload); never in the shipped bundle. Noted because "all our npm deps are permissive" would be a false statement. |
| RF-DETR ball weights `volleyball-ball-tracking-0eo7r/3` | **UNKNOWN** | `ml/pipeline/ball.py:37` | **Open — see below.** |

### Model weights are licensed separately from the libraries that load them

The card was right to call this out, and it is the one item this audit could
**not** close.

- The **library** is settled: Roboflow `inference==1.3.3` is Apache-2.0
  (`ml/modal_app.py:57`).
- The **weights** are not. `volleyball-ball-tracking-0eo7r/3` is a Roboflow
  Universe model, and Universe weights carry whatever license the dataset owner
  set — commonly CC BY 4.0, sometimes CC BY-NC, which would prohibit commercial
  use outright.

This matters more than the ultralytics question if it goes badly: ball tracking
sets **clip boundaries**, so unlike pose it is not replaceable at the margin.

**To close it:** `https://api.roboflow.com/volleyball-ball-tracking-0eo7r?api_key=$ROBOFLOW_API_KEY`,
or the model's Universe page, and read the license field. It needs the key,
which is why this is open — the audit ran without credentials on purpose.

### Findings that look like problems and are not

#### FFmpeg — two separate questions

There are **two** FFmpegs in this system, with different licenses, and
conflating them produces the wrong answer.

1. **The system binary** (`apt-get install ffmpeg`, `Dockerfile.api:9`).
   Debian's build is configured `--enable-gpl --enable-libx264`, making it
   **GPL-2.0-or-later**, and we do use x264 (`vcodec="libx264"`,
   `ml/pipeline/clip.py:102`).

   We invoke it **at arm's length as a separate program** — verified, not
   assumed: `ml/pipeline/audio.py:48` builds an argv and shells out, and
   `ml/pipeline/clip.py`'s `import ffmpeg` is `ffmpeg-python` (Apache-2.0),
   which is an argv *builder* that subprocesses the binary. Nothing links
   `libav*`. Subprocess invocation of a separate program is the standard
   not-a-derivative-work case, so ClipFarm's source is unaffected.

   The obligation that *would* attach is **distribution**, not use: shipping a
   Docker image containing a GPL ffmpeg triggers GPLv2 §3's source offer. We
   currently do not distribute one — Render builds from the repo
   (`render.yaml:94`, `dockerfilePath`) and **no workflow pushes to any
   registry** (verified: no `docker push`, no `ghcr.io`, no
   `docker/build-push-action` anywhere in `.github/workflows/`). If that ever
   changes, this becomes live, and Debian's published sources satisfy it.

   **One case that sweep does not cover, named rather than left implied:**
   `modal deploy` uploads a built image to Modal's registry, and
   `ml/modal_pose.py` `apt_install`s ffmpeg and pip-installs ultralytics into
   it. Whether pushing an image to a third-party host you then run on is
   "conveying" is exactly the sort of question this document reasons about
   elsewhere, and it is not answered here — the GitHub/Render sweep simply does
   not reach it. Flagged as uncovered, not as resolved. It is the same shape as
   the AGPL §13 question and points the same way: the source offer is
   satisfiable, since this repo is public and now carries a `LICENSE`.

2. **The FFmpeg bundled inside `opencv-python-headless`.** Read out of the
   installed wheel rather than assumed: `cv2.getBuildInformation()` reports
   `FFMPEG: YES` with avcodec 59.37.100 / avformat 59.27.100, and the wheel's
   own `LICENSE-3RD-PARTY.txt:243` states *"FFmpeg is redistributed within all
   opencv-python packages"* under **LGPL-2.1**. This one really is linked into
   `cv2`. LGPL-2.1 §6 obligations again attach on distribution only, which we
   do not do. (Read out of `opencv-python-headless==4.10.0.84`, the version
   this repo pins. An earlier version of this paragraph quoted a different
   build's figures — avcodec 58.134.100 — from a locally installed 4.13.0.92,
   which is both the wrong version and, being *older* avcodec from a *newer*
   opencv, self-evidently not the pinned wheel.)

#### Weak copyleft in the npm tree — 5 packages, all fine

Full sweep of 591 installed packages. MPL-2.0 is **file-level** copyleft:
obligations attach only to modified MPL files, and we modify none.

| Package | License | Note |
|---|---|---|
| `@vercel/og`, `axe-core`, `lightningcss`, `lightningcss-win32-x64-msvc` | MPL-2.0 | Unmodified. No obligation. |
| `@img/sharp-libvips-*` (every platform, incl. `-linux-x64`) | Apache-2.0 AND LGPL-3.0-or-later | **Present on the deployed platform.** libvips is not inside the `sharp-<platform>` package; each one pulls a separate `sharp-libvips-<platform>` as an optional dependency, and every one of those is LGPL-3.0-or-later. On Linux that is `@img/sharp-libvips-linux-x64` 1.2.4. LGPL §4/§6 obligations attach on distribution, which we do not do — see below. |

No GPL, AGPL, SSPL, BUSL or CDDL anywhere in the npm tree.

> **Correction, and a limit on the method above.** An earlier version of this
> table listed only `@img/sharp-win32-x64` and called it "a Windows-only
> optional binary — a dev-machine artifact. The deployed Linux build resolves
> `@img/sharp-linux-x64`. Not in the shipped bundle." That was wrong on the
> platform this actually deploys to. `sharp-linux-x64` is Apache-2.0 and does
> *not* contain libvips; it pulls `@img/sharp-libvips-linux-x64`, which is
> LGPL-3.0-or-later and is in the checked-in `package-lock.json`. The
> conclusion did not change — the obligation attaches on distribution either
> way — but the reason given for it was false.
>
> The cause is visible in the numbers: the sweep counted 591 packages and named
> the `win32` variants of both sharp and `@sentry/cli`, i.e. it ran against a
> Windows `node_modules` and then asserted about the Linux tree. **A sweep of
> one platform's installed tree cannot see platform-optional dependencies for
> the others**, and sharp ships eleven of them. Read the lockfile, which is
> platform-independent and committed, rather than a `node_modules` — that is
> how the row above was rebuilt.

#### Everything else

Permissive, no obligations beyond attribution:

- **Python, ML:** `torch` (BSD-3-Clause + Apache-2.0 et al.), `torchvision`
  (BSD), `opencv-python-headless` (Apache-2.0 — the library; its bundled
  FFmpeg is treated above), `numpy` (BSD-3-Clause), `scikit-learn`
  (BSD-3-Clause), `transformers` (Apache-2.0), `Pillow` (MIT-CMU),
  `ffmpeg-python` (Apache-2.0), `inference` / `inference-sdk` (Apache-2.0),
  `tqdm` (MPL-2.0 AND MIT — unmodified), `python-dotenv` (BSD-3-Clause).
- **Python, api:** `fastapi`, `alembic`, `PyJWT`, `pydantic`,
  `pydantic-settings`, `redis`, `sentry-sdk`, `sqlalchemy` (MIT); `uvicorn`,
  `celery`, `httpx` (BSD-3-Clause); `asyncpg`, `boto3`, `modal`,
  `python-multipart` (Apache-2.0).
- **npm:** 476 MIT, 48 Apache-2.0, 22 ISC, 16 BSD-2-Clause, 7 BSD-3-Clause,
  6 BlueOak-1.0.0, plus single instances of Python-2.0 (`argparse`), CC-BY-4.0
  (`caniuse-lite`, a data file), MIT-0, CC0-1.0 and 0BSD.

#### `paddleocr` stays excluded

`Dockerfile.api:63` and the block in `ml/requirements.txt` keep jersey OCR out
of the image, and this audit does not change that. Confirmed still excluded.
CF-7 would reintroduce the question — PaddleOCR is Apache-2.0, so it is not
expected to be a problem, but it should be checked at the time rather than
inherited from this sentence.

---

## Where the declaration lives

- `LICENSE` — verbatim AGPL-3.0 text.
- `package.json`, `web/package.json` — `"license": "AGPL-3.0-or-later"`.
- **Python: there is no metadata slot.** The repo ships no `pyproject.toml`,
  `setup.py` or `setup.cfg` — nothing here is built or published as a
  distribution, so there is no packaging metadata to carry a license field.
  One was deliberately *not* added for this: a root `pyproject.toml` changes
  pytest's rootdir resolution and ruff's config discovery for paths outside
  `api/` and `ml/` (which have their own `ruff.toml`), which is a real risk to
  the pre-commit hook in exchange for a cosmetic declaration. If this repo ever
  becomes an installable package, put `license = "AGPL-3.0-or-later"` in
  `[project]` then.

## Not legal advice

This is an engineer's audit, written to make the position explicit and
reviewable rather than assumed. The ultralytics question in particular would
warrant a real opinion before taking money.
