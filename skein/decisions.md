# Decisions

The team's record of settled questions. Skein reads this on every call and appends to it.
Nobody edits or deletes an entry; to change a decision, append a new one that says so.

Format: one `##` heading per entry, `id · date · short title`, then these lines:

- Question: what was asked, in plain words.
- Answer: one or two sentences someone can act on.
- Decided by: a handle like @maya, `skein` when settled from an earlier entry, or `draft`.
- Why: the reason, short.
- Cites: ids this rests on, comma separated, or `none`. A draft cites what it was drawn from.
- Confirmed: yes or no. Only on drafts; a draft with no never settles anything. To confirm
  one, change the field to yes; or leave it, and the first time an agent asks about it the
  question goes to the owner with the draft attached, and their answer becomes the record.

Entries Skein appends also carry `Context:` and `Route:`: settled (from the record), pending
(waiting on a person, with `Routed to:`) or routed (a person's answer, with `Waited:`, citing
the pending entry it closes). Ids are D- plus four digits, in order. Write new entries at
the bottom.

Drafted by `skein init` from ClipFarmVB/ClipFarm on 2026-10-22: 30 entries, every one a guess
until someone confirms it. Correct the wrong ones in place before confirming.

## D-0001 · 2026-10-22 · Close issue with bare line

- Question: How do I link the GitHub issue this PR resolves?
- Answer: Put a bare, unbolded "Closes #NN" line on its own line in the PR description. Writing "CF-NN" alone does not close the issue on merge.
- Decided by: draft
- Why: The template says GitHub parses that exact form. Without it the issue stays open and its board card does not move.
- Cites: .github/pull_request_template.md, CLAUDE.md
- Confirmed: no

## D-0002 · 2026-10-22 · Reference board card in Why

- Question: Where should the PR link its board card?
- Answer: In the Why section, reference the board card as CF-## (for example CF-53) alongside the problem or task the PR solves.
- Decided by: draft
- Why: The template asks for the board card link in the Why section.
- Cites: .github/pull_request_template.md
- Confirmed: no

## D-0003 · 2026-10-22 · Run pre-commit checks before PR

- Question: Which checks must pass before I mark a PR ready?
- Answer: Run the pre-commit checks (ruff, mypy, eslint, tsc as applicable) and confirm they pass before submitting the PR.
- Decided by: draft
- Why: The template's testing checklist lists these checks as the first item to confirm.
- Cites: .github/pull_request_template.md, #79, #78, #12, #3
- Confirmed: no

## D-0004 · 2026-10-22 · Exercise local changes before PR

- Question: How should I verify a change before opening a PR?
- Answer: Run the app locally (docker compose or the dev server) and exercise the change yourself before asking for review.
- Decided by: draft
- Why: The template includes a checklist item for running the change locally.
- Cites: .github/pull_request_template.md
- Confirmed: no

## D-0005 · 2026-10-22 · Validate pipeline changes with data

- Question: How do I test a change to the pipeline?
- Answer: Validate the pipeline change against a cached trajectory or a test video, and note this in the PR's testing checklist.
- Decided by: draft
- Why: The template lists this as the testing step for pipeline changes.
- Cites: .github/pull_request_template.md, #13, #3, #8
- Confirmed: no

## D-0006 · 2026-10-22 · Explain when not testable locally

- Question: What do I do if a change cannot be tested locally?
- Answer: Check the "Not testable locally" box and explain in the PR description why local testing was not possible.
- Decided by: draft
- Why: The template provides this option and asks for an explanation below it.
- Cites: .github/pull_request_template.md
- Confirmed: no

## D-0007 · 2026-10-22 · Flag risky changes in PR

- Question: What must I call out in the PR description for risky changes?
- Answer: Note any migrations, environment variable changes, or required rebuilds (such as docker compose build) and any known limitations in the risky or follow-up section.
- Decided by: draft
- Why: The template prompts for these items in the section on risks and follow-up work.
- Cites: .github/pull_request_template.md
- Confirmed: no

## D-0008 · 2026-10-22 · Do not commit pytest cache

- Question: Should I commit the .pytest_cache directory to version control?
- Answer: No. Do not commit the .pytest_cache directory to version control.
- Decided by: draft
- Why: The pytest cache README in .pytest_cache says not to commit it; it is generated cache data.
- Cites: .pytest_cache/README.md, api/.pytest_cache/README.md
- Confirmed: no

## D-0009 · 2026-10-22 · Migrate shared databases deliberately

- Question: May I run migrations against a shared or production database?
- Answer: No. Auto-migrate only runs against a local database. Migrate shared or production targets through Render's preDeployCommand or a person running alembic upgrade head.
- Decided by: draft
- Why: Local-only auto-migrate (CF-189); shared targets are migrated deliberately, per ARCHITECTURE.md.
- Cites: ARCHITECTURE.md, CLAUDE.md, README.md
- Confirmed: no

## D-0010 · 2026-10-22 · Use IF EXISTS on drops

- Question: How should a migration drop a table or index?
- Answer: Use DROP ... IF EXISTS, because dev databases drift from what earlier migrations claim they created.
- Decided by: draft
- Why: Migration 008 hit a missing index that migration 005 claimed to create.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0011 · 2026-10-22 · Express velocity thresholds per second

- Question: What unit should new velocity thresholds use?
- Answer: Use px/second, never px/frame, and keep sampling fps-aware.
- Decided by: draft
- Why: Same footage at 60 fps halves px/frame velocities and silently breaks thresholds (CF-34).
- Cites: ARCHITECTURE.md, #3, ml/pipeline/ball.py, ml/pipeline/score.py
- Confirmed: no

## D-0012 · 2026-10-22 · Bump tracking cache version on change

- Question: Did my change alter how ball tracking works?
- Answer: Bump ball.TRACKING_CACHE_VERSION, because the cache key cannot see changes to tracking logic. Without the bump, old tracks are replayed.
- Decided by: draft
- Why: Tuning changes otherwise replay tracks built by old code (CF-231).
- Cites: ARCHITECTURE.md, README.md, ml/pipeline/ball.py
- Confirmed: no

## D-0013 · 2026-10-22 · Keep ml free of app config

- Question: Can code under ml/ import app settings?
- Answer: No. ml/ never imports app config; pass tunables in as keyword arguments instead.
- Decided by: draft
- Why: Keeps pipeline code pure and unit-testable.
- Cites: ARCHITECTURE.md, ml/pipeline/dead_time.py
- Confirmed: no

## D-0014 · 2026-10-22 · Keep inference pinned to 1.3.3

- Question: May I upgrade or unpin the inference package?
- Answer: Keep inference pinned to 1.3.3. Pip's resolver may backtrack into a broken state otherwise.
- Decided by: draft
- Why: The resolver once picked a version predating the ball model's architecture (CF-33).
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0015 · 2026-10-22 · Never proxy uploads through the api

- Question: Should video bytes pass through the FastAPI server?
- Answer: No. Use presigned R2 URLs so the browser uploads directly, then confirm with a HEAD before enqueueing the job.
- Decided by: draft
- Why: Multi-GB request bodies were a resource cost and blocked serverless hosting.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0016 · 2026-10-22 · Verify uploaded video content type

- Question: Is the signed Content-Type header enough to trust an upload?
- Answer: No. After the HEAD, do a ranged read to confirm the stored object is a real video container, and check size against the stored object.
- Decided by: draft
- Why: A signed Content-Type binds the header, not the bytes, and size cannot be signed (CF-244).
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0017 · 2026-10-22 · Store public URLs, presign on read

- Question: What should the database hold for R2 object locations?
- Answer: Store the public-form URL and generate presigned links in the routers on read. Never store presigned URLs or secrets in the database.
- Decided by: draft
- Why: Storage stays private and links expire; the DB never holds secrets.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0018 · 2026-10-22 · Post-clip steps must not fail run

- Question: If the condense stage errors, should the game fail?
- Answer: No. Clips are saved before condense, so condense failures should be logged while the game still reaches ready.
- Decided by: draft
- Why: A nice-to-have must never hold the core deliverable hostage.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0019 · 2026-10-22 · Pin audio rate when concatenating

- Question: What settings should clip parts use before stream-copy concat?
- Answer: Use identical codec parameters across parts and pin audio to ar=48000, since mixed rates glitch at copy-concat joins.
- Decided by: draft
- Why: A/V sync gotchas were found and handled during condense stitching.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0020 · 2026-10-22 · Never commit directly to main

- Question: Can I commit my change straight to main?
- Answer: No. Work on a branch and open a PR; squash on merge to keep history linear.
- Decided by: draft
- Why: Stated as a repo convention in CLAUDE.md under Branch and PR conventions.
- Cites: CLAUDE.md
- Confirmed: no

## D-0021 · 2026-10-22 · Branch names follow category pattern

- Question: How should I name my branch?
- Answer: Use category/CF-##-short-description, with category one of devops, ball-detection, deadtime, eval, docs, fix, ci, or chore.
- Decided by: draft
- Why: Convention listed in CLAUDE.md; the category list is given there.
- Cites: CLAUDE.md, #438
- Confirmed: no

## D-0022 · 2026-10-22 · PR titles use type(scope) form

- Question: What format should the PR title take?
- Answer: Use type(scope): CF-## description for new work, and match the documented form rather than older PRs that deviate.
- Decided by: draft
- Why: CLAUDE.md notes older PRs use other forms but says to follow the documented one.
- Cites: CLAUDE.md, #77
- Confirmed: no

## D-0023 · 2026-10-22 · Install pinned tooling versions

- Question: How do I install ruff, mypy, and pytest locally?
- Answer: Install from requirements-tooling.txt rather than unpinned pip installs, so local results match CI.
- Decided by: draft
- Why: Pinned versions match CI (CF-92); unpinned installs can disagree with CI.
- Cites: CLAUDE.md
- Confirmed: no

## D-0024 · 2026-10-22 · Enable the repo pre-commit hook

- Question: Do I need to set up the pre-commit hook in my clone?
- Answer: Yes. Run git config core.hooksPath .hooks once per clone and make sure the hook is executable, since git skips non-executable hooks silently.
- Decided by: draft
- Why: The hook is not installed by default; without it commits get no local checks.
- Cites: CLAUDE.md
- Confirmed: no

## D-0025 · 2026-10-22 · Run npm ci in new worktrees

- Question: What do I do first in a fresh git worktree?
- Answer: Run npm ci at the repo root before committing, because the hook's eslint, tsc, and vitest steps fail without node_modules.
- Decided by: draft
- Why: Missing installs look like code failures; CLAUDE.md says this bites every new worktree.
- Cites: CLAUDE.md
- Confirmed: no

## D-0026 · 2026-10-22 · Use Python 3.11 for the API

- Question: Which Python version should I use for api tests and installs?
- Answer: Use Python 3.11, not 3.12 or newer, because numpy 1.26.4 has no wheels for later versions.
- Decided by: draft
- Why: CI and Dockerfile.api run 3.11; api/requirements-dev.txt states it.
- Cites: CLAUDE.md, README.md, api/requirements-dev.txt
- Confirmed: no

## D-0027 · 2026-10-22 · Install dev requirements before pytest

- Question: Why do some API tests skip, and what do I install?
- Answer: Install api/requirements-dev.txt, not requirements.txt, so importorskip-guarded tests run instead of skipping silently.
- Decided by: draft
- Why: Without test-only deps tests skip and the run still reports green; production image builds from requirements.txt.
- Cites: CLAUDE.md, README.md, api/requirements-dev.txt
- Confirmed: no

## D-0028 · 2026-10-22 · Coordinate migration revision order

- Question: Can I add an Alembic migration alongside another open PR?
- Answer: Coordinate the revision order with other PRs first, because the chain is linear and parallel migrations collide; merge before running against shared Supabase.
- Decided by: draft
- Why: CLAUDE.md states the chain is linear and parallel migration PRs will collide.
- Cites: CLAUDE.md
- Confirmed: no

## D-0029 · 2026-10-22 · Reporting must not break processing

- Question: What if a progress write, metric, or log call fails in new code?
- Answer: Wrap progress writes, metrics, and logging so a failure degrades the signal instead of killing the run.
- Decided by: draft
- Why: Reviewers cite this consistently, per CLAUDE.md Code posture.
- Cites: CLAUDE.md, #15
- Confirmed: no

## D-0030 · 2026-10-22 · Do not edit claude-review workflow in PRs

- Question: Can a PR change .github/workflows/claude-review.yml and still get an automated review?
- Answer: No. The review skips unless the workflow file is byte-identical to main, so do not expect review of a PR that edits it; a person should decide such changes.
- Decided by: draft
- Why: Guard against PRs rewriting the workflow to exfiltrate secrets, per CLAUDE.md.
- Cites: CLAUDE.md
- Confirmed: no
