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

## D-0001 · 2026-10-22 · Bare issue closing line

- Question: How should a pull request reference the issue it closes?
- Answer: Put "Closes #NN" on its own line with no bold or extra text, because GitHub only parses that exact form. A bare "CF-NN" reference does not close the issue.
- Decided by: draft
- Why: Template comment says the exact form is what GitHub parses; otherwise the issue stays open and its board card does not move.
- Cites: .github/pull_request_template.md, CLAUDE.md
- Confirmed: no

## D-0002 · 2026-10-22 · Link board card as CF

- Question: How should a pull request link its board card?
- Answer: Reference the board card as CF-## in the Why section and in the Board line.
- Decided by: draft
- Why: Template asks for the board card link in the Why section and the Board field.
- Cites: .github/pull_request_template.md
- Confirmed: no

## D-0003 · 2026-10-22 · Pre-commit checks before opening

- Question: Which checks should pass before a pull request is opened?
- Answer: Run the pre-commit checks (ruff, mypy, eslint, tsc) and confirm they pass before submitting the pull request.
- Decided by: draft
- Why: Listed as the first testing item in the PR template.
- Cites: .github/pull_request_template.md
- Confirmed: no

## D-0004 · 2026-10-22 · Pipeline changes need validation

- Question: What must be done to verify a change to the pipeline?
- Answer: Validate the pipeline change against a cached trajectory or a test video, and record that in the testing section.
- Decided by: draft
- Why: PR template lists this as a testing requirement for pipeline changes.
- Cites: .github/pull_request_template.md
- Confirmed: no

## D-0005 · 2026-10-22 · Explain changes not testable locally

- Question: What should be written if a change cannot be run locally?
- Answer: State that the change is not testable locally and explain why in the testing section.
- Decided by: draft
- Why: Template offers this option explicitly as an alternative to local runs.
- Cites: .github/pull_request_template.md
- Confirmed: no

## D-0006 · 2026-10-22 · Flag migrations and env changes

- Question: What risky changes must a pull request call out?
- Answer: Call out in the risky section any migrations, environment variable changes, or need for a rebuild such as docker compose build, along with known limitations.
- Decided by: draft
- Why: Template asks for these in the risk and follow-up section.
- Cites: .github/pull_request_template.md
- Confirmed: no

## D-0007 · 2026-10-22 · Route uploads directly to storage

- Question: Should video upload bytes pass through the API?
- Answer: No. The browser PUTs video bytes to R2 using presigned URLs, and the API confirms the object with a HEAD before enqueueing the job.
- Decided by: draft
- Why: Multi-GB bodies were a resource cost and blocked serverless hosting; the size limit is enforced before the transfer. From ARCHITECTURE.md.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0008 · 2026-10-22 · Use per-second velocity thresholds

- Question: What units should velocity thresholds in the ball pipeline use?
- Answer: Use px/second, never px/frame, and keep frame sampling fps-aware.
- Decided by: draft
- Why: Footage at 60 fps halves px/frame velocities and silently breaks thresholds; per-second units make tuning portable (CF-34).
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0009 · 2026-10-22 · Bump tracking cache version on change

- Question: Do I need to change the ball tracking cache when tracking behavior changes?
- Answer: Yes. Bump ball.TRACKING_CACHE_VERSION whenever tracking logic changes, or the old cached tracks will be replayed.
- Decided by: draft
- Why: The content-hash cache key cannot see code changes; bumping the version orphans stale entries (CF-231).
- Cites: ARCHITECTURE.md, README.md
- Confirmed: no

## D-0010 · 2026-10-22 · Do not auto-migrate shared databases

- Question: May the API container run migrations on startup?
- Answer: Only against a local database. Shared or production databases must be migrated deliberately, through preDeployCommand or a person running alembic upgrade head.
- Decided by: draft
- Why: scripts/auto_migrate.py is limited to local databases (CF-189).
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0011 · 2026-10-22 · Guard destructive drops with IF EXISTS

- Question: How should destructive schema drops be written?
- Answer: Use IF EXISTS on drops, since dev databases drift from what earlier migrations claim.
- Decided by: draft
- Why: Migration 008 hit a missing index that migration 005 claimed to create.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0012 · 2026-10-22 · Keep ml code free of app config

- Question: Can code under ml/ read application settings?
- Answer: No. ml/ never imports app config; pass tunables in as keyword arguments.
- Decided by: draft
- Why: Keeps pipeline functions pure and unit-testable.
- Cites: ml/pipeline/dead_time.py, ARCHITECTURE.md
- Confirmed: no

## D-0013 · 2026-10-22 · Use deterministic condensed video key

- Question: What R2 key should a condensed video be written to?
- Answer: Use condensed/{game_id}.mp4 so task retries overwrite the object instead of orphaning it.
- Decided by: draft
- Why: Cheap idempotency for task retries.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0014 · 2026-10-22 · Condense failure must not fail game

- Question: If the condense stage raises, should the whole game fail?
- Answer: No. Clips are saved before condense runs; condense errors are logged and the game still goes ready.
- Decided by: draft
- Why: A nice-to-have must never hold the core deliverable hostage.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0015 · 2026-10-22 · Store public-form URLs, serve presigned

- Question: What should database rows hold for stored media URLs?
- Answer: Store {r2_public_url}/{key} and convert to presigned URLs when serving. Never store secrets in the database.
- Decided by: draft
- Why: Storage stays private and links expire.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0016 · 2026-10-22 · Use sync DB engine in workers

- Question: Which database engine should Celery task code use?
- Answer: Use the sync engine from _sync_db.py in Celery tasks; request handlers use async SQLAlchemy.
- Decided by: draft
- Why: Celery workers have no event loop.
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0017 · 2026-10-22 · Set timeouts on S3 transfers

- Question: Should boto3 storage calls have timeouts?
- Answer: Yes. Set connect and read timeouts with retries so hung transfers become retryable failures.
- Decided by: draft
- Why: With a solo worker pool, one hung transfer blocks the whole queue (CF-32).
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0018 · 2026-10-22 · Keep inference pinned to 1.3.3

- Question: Can I upgrade or unpin the inference dependency?
- Answer: Keep inference pinned at 1.3.3. Resolver backtracking can succeed into a broken state, so do not loosen it casually.
- Decided by: draft
- Why: Pip backtracked to a version predating the ball model's architecture (CF-33).
- Cites: ARCHITECTURE.md
- Confirmed: no

## D-0019 · 2026-10-22 · Never commit directly to main

- Question: Can I commit my change straight to the main branch?
- Answer: No. Work on a branch and open a PR; squash on merge to keep history linear.
- Decided by: draft
- Why: Stated as a convention in CLAUDE.md under branch and PR conventions.
- Cites: CLAUDE.md
- Confirmed: no

## D-0020 · 2026-10-22 · Branch name follows category pattern

- Question: What should I name my branch?
- Answer: Use category/CF-##-short-description, with category one of devops, ball-detection, deadtime, eval, docs, fix, ci, or chore.
- Decided by: draft
- Why: CLAUDE.md lists the branch name format and the categories in use.
- Cites: CLAUDE.md
- Confirmed: no

## D-0021 · 2026-10-22 · PR title uses documented form

- Question: How should I title my pull request?
- Answer: Use type(scope): CF-## description for new work, and match the documented form rather than the nearest older example.
- Decided by: draft
- Why: CLAUDE.md notes older PRs deviate, and says to follow the documented form.
- Cites: CLAUDE.md
- Confirmed: no

## D-0022 · 2026-10-22 · Wire up pre-commit hook once

- Question: Have I enabled the local pre-commit checks in this clone?
- Answer: Run git config core.hooksPath .hooks once per clone, and confirm the hook is executable, since git skips non-executable hooks silently.
- Decided by: draft
- Why: Nothing sets the hook path automatically; a clone without it commits with no local checks.
- Cites: CLAUDE.md
- Confirmed: no

## D-0023 · 2026-10-22 · Install pinned tooling requirements

- Question: How should I install ruff, mypy, and pytest?
- Answer: Install from requirements-tooling.txt with python -m pip, not a bare pip install of unpinned tools, so local checks match CI.
- Decided by: draft
- Why: Pinned versions are what CI runs (CF-92); unpinned installs can disagree with CI.
- Cites: CLAUDE.md
- Confirmed: no

## D-0024 · 2026-10-22 · Run npm ci in new worktrees

- Question: Do I need to do anything before running checks in a fresh worktree?
- Answer: Run npm ci at the repo root first, because without node_modules the eslint, tsc, and vitest steps fail or hang.
- Decided by: draft
- Why: CLAUDE.md says this bites every new worktree and looks like a code problem.
- Cites: CLAUDE.md
- Confirmed: no

## D-0025 · 2026-10-22 · Use dev requirements for tests

- Question: Which requirements file should I install to run the API tests?
- Answer: Install api/requirements-dev.txt, not requirements.txt, so importorskip-guarded tests do not skip silently; use Python 3.11.
- Decided by: draft
- Why: Test-only deps stay out of requirements.txt on purpose; numpy 1.26.4 has no wheel for 3.12+.
- Cites: CLAUDE.md
- Confirmed: no

## D-0026 · 2026-10-22 · Coordinate migration revision order

- Question: What should I do when adding an Alembic migration?
- Answer: Coordinate the revision order with other open PRs that add migrations, since the chain is linear and parallel revisions collide; merge before running against shared Supabase.
- Decided by: draft
- Why: CLAUDE.md states the chain is linear and parallel migration PRs will collide.
- Cites: CLAUDE.md, api/alembic/versions/
- Confirmed: no

## D-0027 · 2026-10-22 · Apply migrations to Supabase deliberately

- Question: Should I run migrations against the shared Supabase database?
- Answer: Only as a deliberate, separate alembic upgrade head step; docker compose does not migrate Supabase, and that schema change is not automatic.
- Decided by: draft
- Why: auto_migrate.py skips non-local databases (CF-189); CLAUDE.md says applying there stays deliberate.
- Cites: CLAUDE.md, api/scripts/auto_migrate.py, README.md
- Confirmed: no

## D-0028 · 2026-10-22 · Reporting must not break processing

- Question: What should happen when progress writes, metrics, or logging fail?
- Answer: Wrap them so a failure degrades the signal rather than killing the run, matching existing code.
- Decided by: draft
- Why: Reviewers cite this consistently, per CLAUDE.md code posture section.
- Cites: CLAUDE.md
- Confirmed: no

## D-0029 · 2026-10-22 · Read pipeline docs before detection changes

- Question: Do I need to read the docs before changing detection or pipeline code?
- Answer: Yes; read the relevant README Key Concepts section first, since it exists to prevent changes that break rally boundaries.
- Decided by: draft
- Why: CLAUDE.md directs this before changing detection or pipeline code.
- Cites: CLAUDE.md, README.md
- Confirmed: no

## D-0030 · 2026-10-22 · No timing claims from fast runs

- Question: Can I report timing or memory numbers from a run using the fast overlay?
- Answer: No. Fast overlay runs only check that the pipeline works; any timing or memory claim needs default resource limits.
- Decided by: draft
- Why: The fast overlay gives up the fidelity the defaults exist for.
- Cites: README.md
- Confirmed: no
