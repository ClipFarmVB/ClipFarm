# Decisions

The team's record of settled questions. Skein reads this on every call and appends to it.

Format: one `##` heading per entry, `id · date · short title`, then these lines:

- Question: what was asked, in plain words.
- Answer: one or two sentences someone can act on.
- Decided by: a handle like @maya, `skein` when settled from an earlier entry, or `draft`.
- Why: the reason, short, and for a draft the sources with the date each last changed.
- Cites: ids this rests on, comma separated, or `none`. A draft cites what it was drawn from.
- Confirmed: yes or no. A draft with no never settles anything. To confirm one, change the
  field to yes; or leave it, and the first time an agent asks about it the question goes to
  the owner with the draft attached, and their answer becomes the record.

Two kinds of entry, two rules. A draft (`Confirmed: no`) is a guess: edit it, or delete it,
freely. A confirmed entry is the record: never edited or deleted, by people or by Skein. To
change one, append a newer entry that cites it; the newer entry overrides it from then on.

Entries Skein appends also carry `Context:` and `Route:`: settled (from the record), pending
(waiting on a person, with `Routed to:`) or routed (a person's answer, with `Waited:`, citing
the pending entry it closes). Ids are D- plus four digits, in order. Write new entries at
the bottom.

Drafted by `skein init` from ClipFarmVB/ClipFarm on 2026-10-22: 30 entries, the most important
of 151 found, every one a guess until someone confirms it.

## D-0001 · 2026-10-22 · Run ml tests in CI

- Question: Should I rely on CI to catch breakage in the ml eval harness metrics code?
- Answer: Run the ml unit tests with pytest in CI and pre-commit, since lint alone does not exercise the harness logic. A reviewer required adding a pytest step so changes to the interval math cannot pass green CI untested.
- Decided by: draft
- Why: Review by Ollienel777 on PR #77 noted CI and the pre-commit hook only ran ruff and mypy; the follow-up commit 4700feb added the step. Sources: #77 (merged 2026-07-24), ml/eval/harness.py (last changed 2026-10-20), ml/tests/test_eval_metrics.py (last changed 2026-08-05), #15 (merged 2026-07-27).
- Cites: #77, ml/eval/harness.py, ml/tests/test_eval_metrics.py, #15
- Confirmed: no

## D-0002 · 2026-10-22 · Migration chain must have one head

- Question: Can I add a migration whose revision number is already taken on main, or leave the chain with two heads?
- Answer: No. Each migration must have a unique revision and the chain must resolve to a single head; test_migration_chain must stay green before merging, and PENDING_UPSTREAM is kept to catch the next collision.
- Decided by: draft
- Why: Reviewer found 014 duplicated with main, so Alembic only warned and the posts table would never be created; PR #190 notes the chain renumbered twice. Sources: #190 (merged 2026-08-28), api/tests/test_migration_chain.py (last changed 2026-08-28), #15 (merged 2026-07-27), api/alembic/versions/009_game_progress.py (last changed 2026-07-29).
- Cites: #190, api/tests/test_migration_chain.py, #15, api/alembic/versions/009_game_progress.py
- Confirmed: no

## D-0003 · 2026-10-22 · Run pre-commit checks before PR

- Question: Which checks must pass before I open a pull request?
- Answer: Run ruff, mypy, eslint and tsc and make sure they pass before the PR is submitted.
- Decided by: draft
- Why: PR template checklist; reviewed PRs record these as passing before merge. Sources: #8 (merged 2026-07-10), #12 (merged 2026-07-10).
- Cites: #8, #12
- Confirmed: no

## D-0004 · 2026-10-22 · Rebuild stale PRs without squashing

- Question: When a PR's base was force-pushed and its diff is unusable, how should I replay it?
- Answer: Replay the original commits onto current main with git rebase --onto, preserving the original author and commit messages. Do not squash them or edit their content.
- Decided by: draft
- Why: main was force-pushed to rewritten history, so older branches could no longer be diffed cleanly; these replays were the team's chosen fix. Sources: #403 (merged 2026-08-30), #402 (merged 2026-08-30), #404 (merged 2026-08-30).
- Cites: #403, #402, #404
- Confirmed: no

## D-0005 · 2026-10-22 · Acquire lock after abandoned check

- Question: Where in process_game_task should the game lock be acquired?
- Answer: Acquire the lock after the existing _abandoned() entry check and before the try block, and release it in finally. Do not reimplement the sync_get_game None checks that CF-69 already provides.
- Decided by: draft
- Why: Duplicate deliveries must be logged no-ops, and CF-69 already handles delete-mid-flight; the reviewer required removing duplicates. Sources: #105 (merged 2026-07-31), api/app/workers/tasks.py (last changed 2026-10-20).
- Cites: #105, api/app/workers/tasks.py
- Confirmed: no

## D-0006 · 2026-10-22 · Probe Python by running it

- Question: How should the pre-commit hook pick a Python interpreter on Windows?
- Answer: Find the interpreter by running each candidate, for example with -c "", and pick the first that actually runs. Do not use a command -v or existence check.
- Decided by: draft
- Why: On Windows the Store alias stubs pass an existence check but exit 49, so existence checks pick a broken python. Sources: #408 (merged 2026-08-30), .hooks/pre-commit (last changed 2026-10-15).
- Cites: #408, .hooks/pre-commit
- Confirmed: no

## D-0007 · 2026-10-22 · Keep shallow healthz for Render

- Question: Which health route should Render's health check call?
- Answer: Render's healthCheckPath must point at the shallow /healthz route. The deep /health route that checks DB and Redis is reserved for the external uptime monitor and must not be used by Render.
- Decided by: draft
- Why: A transient Supabase or Redis blip would otherwise cycle a healthy API or fail a deploy. Sources: #107 (merged 2026-07-24), #98 (merged 2026-08-01), api/app/main.py (last changed 2026-09-16), render.yaml (last changed 2026-10-15).
- Cites: #107, #98, api/app/main.py, render.yaml
- Confirmed: no

## D-0008 · 2026-10-22 · Re-read visibility section after #481

- Question: Does the visibility documentation still hold once PR #481 merges?
- Answer: No. Re-read the visibility section of the inventory when #481 merges, because it adds clip visibility writes and a PUBLIC_POSTING_ENABLED gate.
- Decided by: draft
- Why: The doc says its visibility section is a countdown to #481 and #482 and must be re-read then. Sources: #481 (merged 2026-09-16), #482 (merged 2026-09-15), docs/privacy/data-inventory.md (last changed 2026-09-16).
- Cites: #481, #482, docs/privacy/data-inventory.md
- Confirmed: no

## D-0009 · 2026-10-22 · Name unmeasured risks in code

- Question: What should I do when a change cannot be validated against the downstream path before merge?
- Answer: Do not leave the risk silent. Document it as an accepted risk in the code comment, naming the unmeasured path, and track it.
- Decided by: draft
- Why: Ollienel777's review of #118 offered this as the minimum when the highlight-path harness could not be run; the reviewer then flagged that the accepted risk was tracked nowhere. Sources: #118 (merged 2026-08-06).
- Cites: #118
- Confirmed: no

## D-0010 · 2026-10-22 · Scrub passwords in every rendering

- Question: Does secret scrubbing only need to catch a connection string in the exact form it was configured?
- Answer: No. Scrubbing must also catch the password component on its own, so the same password is redacted when it appears in a reconstructed DSN, a different query string, or a driver's separate host and password fields.
- Decided by: draft
- Why: Follow-up hardening noted on #107; the gap was reproduced before the fix and reviewed by chessdudeo7. Sources: #131 (merged 2026-08-04), #107 (merged 2026-07-24).
- Cites: #131, #107
- Confirmed: no

## D-0011 · 2026-10-22 · Gate debug hooks behind settings.debug

- Question: Should a new debug or test-trigger hook be exposed in the API or worker?
- Answer: Gate it behind settings.debug so it only registers when that toggle is on. The worker's debug.trigger_error task and the API's /debug/sentry-error both follow this rule.
- Decided by: draft
- Why: Reviewer enforcement in the #107 re-review, which asked for the worker task to match the API hook and be gated behind settings.debug. Sources: #107 (merged 2026-07-24), #109 (merged 2026-08-04).
- Cites: #107, #109
- Confirmed: no

## D-0012 · 2026-10-22 · Rebase onto main before merging

- Question: Should a PR branch be merged while behind main?
- Answer: Rebase the branch onto main before merge so it is not behind. Do not merge a branch that duplicates work already merged to main.
- Decided by: draft
- Why: A branch 21 commits behind main duplicated merged CF-69 code and would conflict; the reviewer required a rebase. Sources: #105 (merged 2026-07-31).
- Cites: #105
- Confirmed: no

## D-0013 · 2026-10-22 · Keep CPU fallback for Modal

- Question: What should happen to ball tracking if Modal fails?
- Answer: Keep the automatic fallback to local CPU tracking on any Modal failure so a Modal outage never blocks processing.
- Decided by: draft
- Why: PR #8 design: graceful CPU fallback so a Modal outage never blocks processing. Sources: #8 (merged 2026-07-10).
- Cites: #8
- Confirmed: no

## D-0014 · 2026-10-22 · Compare pre-gate and post-gate

- Question: Should I read only the post-gate numbers when a regression shows up?
- Answer: Always compare pre-gate and post-gate together. Coverage lost pre-gate is a detection problem; lost only post-gate is a threshold problem, and the fixes are opposite.
- Decided by: draft
- Why: The pair localizes a regression to detection or scoring. Sources: ml/eval/README.md (last changed 2026-10-20).
- Cites: ml/eval/README.md
- Confirmed: no

## D-0015 · 2026-10-22 · Do not promise erasure yet

- Question: Can I write or claim that a user's data is erased on request?
- Answer: Do not promise erasure on request. Account deletion is not implemented and the schema blocks it, so no policy or user-facing text may claim it.
- Decided by: draft
- Why: Deletion is blocked by missing endpoint, NO ACTION foreign keys, and the Supabase mirror. Sources: docs/privacy/data-inventory.md (last changed 2026-09-16).
- Cites: docs/privacy/data-inventory.md
- Confirmed: no

## D-0016 · 2026-10-22 · Do not add root pyproject.toml

- Question: Should I add a root pyproject.toml, setup.py or setup.cfg for license metadata?
- Answer: Do not add a root pyproject.toml, setup.py or setup.cfg. It changes pytest rootdir resolution and ruff config discovery and risks the pre-commit hook. Add packaging metadata only if the repo becomes an installable package.
- Decided by: draft
- Why: Deliberately not added: a root pyproject.toml would affect pytest and ruff for paths outside api/ and ml/. Sources: docs/licensing.md (last changed 2026-09-15).
- Cites: docs/licensing.md
- Confirmed: no

## D-0017 · 2026-10-22 · Progress reporting must never fail processing

- Question: If writing progress or running an on-progress callback raises an error, should it stop the processing pipeline?
- Answer: No. Progress reporting must be non-fatal: exception-guard callbacks and progress writes so a failure never breaks processing.
- Decided by: draft
- Why: Reviewers on #15 described this as the pipeline's existing reporting posture, applied consistently across ball.py, clip.py and the progress writer. Sources: CLAUDE.md (last changed 2026-10-15), #15 (merged 2026-07-27), api/app/workers/progress.py (last changed 2026-07-29), ml/pipeline/ball.py (last changed 2026-10-20).
- Cites: CLAUDE.md, #15, api/app/workers/progress.py, ml/pipeline/ball.py
- Confirmed: no

## D-0018 · 2026-10-22 · Keep lock TTL above visibility timeout

- Question: How should the worker lock TTL compare to the Celery visibility timeout?
- Answer: The process lock TTL must stay greater than the Celery visibility timeout. Keep the invariant covered by test_lock_ttl_exceeds_visibility_timeout so it cannot drift.
- Decided by: draft
- Why: A redelivery during an over-running task must not acquire a lock that has already expired; the reviewer asked for the test to pin it. Sources: #105 (merged 2026-07-31), api/tests/test_worker_safety.py (last changed 2026-09-07), api/app/config.py (last changed 2026-10-20).
- Cites: #105, api/tests/test_worker_safety.py, api/app/config.py
- Confirmed: no

## D-0019 · 2026-10-22 · Harness must run offline cache

- Question: When running the eval harness with --offline, how should the ball track be obtained?
- Answer: Pass the r2_key to the cached ball track lookup so the cache and Modal path are used. Do not fall through to a CPU re-track.
- Decided by: draft
- Why: Review by Ollienel777 on PR #77 found --offline silently fell into a roughly 30-minute CPU re-track; commit 1d513c9 fixed it. Sources: #77 (merged 2026-07-24), ml/eval/harness.py (last changed 2026-10-20).
- Cites: #77, ml/eval/harness.py
- Confirmed: no

## D-0020 · 2026-10-22 · Keep ml pipeline free of database

- Question: Can code under ml/ read or write the database directly, or report progress through its own DB calls?
- Answer: No. ml/ must stay DB-free: report progress through optional callbacks that default to no-ops, and inject the database writer from the api side.
- Decided by: draft
- Why: Stated in #15's description and praised in its reviews as the layering that keeps ml/ pure and testable. Sources: #15 (merged 2026-07-27), ml/pipeline/ball.py (last changed 2026-10-20), api/app/workers/progress.py (last changed 2026-07-29).
- Cites: #15, ml/pipeline/ball.py, api/app/workers/progress.py
- Confirmed: no

## D-0021 · 2026-10-22 · Re-read user row on conflict

- Question: How should I insert a user row that may already exist after a signup race?
- Answer: Read the users row first and insert only when it is missing. On IntegrityError, roll back and re-read the row; never relink an existing row to a different id.
- Decided by: draft
- Why: A signup race raised users_email_key because on_conflict_do_nothing with index_elements=['id'] does not cover the email unique index (#560, CF-295). Sources: #560 (merged 2026-09-27), api/app/auth.py (last changed 2026-10-15), api/tests/test_auth_provisioning_pg.py (last changed 2026-09-27).
- Cites: #560, api/app/auth.py, api/tests/test_auth_provisioning_pg.py
- Confirmed: no

## D-0022 · 2026-10-22 · Fixer replies per finding, never fixed

- Question: What do I write back when a fixer addresses a review finding?
- Answer: Reply to each finding separately and never write 'fixed'. The semi-cold review round decides whether the finding is resolved.
- Decided by: draft
- Why: Fixes cannot run in parallel in the overnight loop, and the review round is the judge (#568, docs/overnight/RULES.md). Sources: #568 (merged 2026-09-27), docs/overnight/RULES.md (last changed 2026-10-15).
- Cites: #568, docs/overnight/RULES.md
- Confirmed: no

## D-0023 · 2026-10-22 · Push PR branches through guard

- Question: How do I push a commit to a PR branch in the overnight loop?
- Answer: Every push to a PR branch must go through the push guard, which compares the remote head with the head the work started from.
- Decided by: draft
- Why: A plain git push does not catch a branch that was added to or rewound; the guard is tested against real git (#568). Sources: #568 (merged 2026-09-27), docs/overnight/RULES.md (last changed 2026-10-15), api/tests/test_overnight_push_guard.py (last changed 2026-10-15).
- Cites: #568, docs/overnight/RULES.md, api/tests/test_overnight_push_guard.py
- Confirmed: no

## D-0024 · 2026-10-22 · Keep headless opencv pinned

- Question: May I unpin or bump opencv-python-headless in the ball image?
- Answer: Keep opencv-python-headless pinned at 4.10.0.84 in ml/modal_app.py; do not unpin or bump it.
- Decided by: draft
- Why: inference pins sibling opencv distributions to <=4.10.0.84; unpinned headless resolves to 5.0.0.93 and breaks cv2. Sources: #438 (merged 2026-09-07), ml/modal_app.py (last changed 2026-10-15).
- Cites: #438, ml/modal_app.py
- Confirmed: no

## D-0025 · 2026-10-22 · Reuse the ownership helper

- Question: When I need to check that a caller may use a referenced player, should I write a new ownership check?
- Answer: Reuse the existing helper get_owned_player rather than writing a second Player to Team to owner walk. Duplicated authorization rules drift apart.
- Decided by: draft
- Why: Stated in PR #239; reviewer confirmed update_player and create_player also check ownership and that no other route writes player_id. Sources: #404 (merged 2026-08-30), api/app/services/access.py (last changed 2026-09-16), api/tests/test_clip_tag_access.py (last changed 2026-08-28), #239 (merged 2026-08-28), api/app/routers/players.py (last changed 2026-08-30), api/app/routers/clips.py (last changed 2026-10-15).
- Cites: #404, api/app/services/access.py, api/tests/test_clip_tag_access.py, #239, api/app/routers/players.py, api/app/routers/clips.py
- Confirmed: no

## D-0026 · 2026-10-22 · Share links never carry attachment

- Question: Should I add a download or attachment option to the /share endpoint so one link can both play and download?
- Answer: No. Do not add an attachment flag to /share; keep downloads on the separate /clips/{clip_id}/download endpoint, and share links must not carry attachment.
- Decided by: draft
- Why: A share link is meant to be passed around and play inline, so a boolean on /share would let one caller's parameter decide another caller's link behaviour (PR #305 description). Sources: #305 (merged 2026-08-27), api/app/routers/clips.py (last changed 2026-10-15).
- Cites: #305, api/app/routers/clips.py
- Confirmed: no

## D-0027 · 2026-10-22 · Keep Render auto-deploy disabled

- Question: Should a merge to main automatically deploy the Render services?
- Answer: No. Auto-deploy must stay off on all Render services, so production deploys are triggered by hand. The API applies Alembic migrations on deploy, so an automatic deploy would migrate production on every merge.
- Decided by: draft
- Why: Migrations run in the API deploy step; the reviewer confirmed autoDeploy is false on all services with rationale recorded. Sources: #98 (merged 2026-08-01), render.yaml (last changed 2026-10-15).
- Cites: #98, render.yaml
- Confirmed: no

## D-0028 · 2026-10-22 · Use fromService for Key Value URLs

- Question: How should the Render blueprint reference the Key Value connection string?
- Answer: Reference the Key Value connection string with fromService, never fromDatabase. fromDatabase cannot reference a Key Value service and will fail on apply.
- Decided by: draft
- Why: The reviewer found six fromDatabase references that failed on apply; the fix switched them to fromService. Sources: #98 (merged 2026-08-01), render.yaml (last changed 2026-10-15).
- Cites: #98, render.yaml
- Confirmed: no

## D-0029 · 2026-10-22 · Use bare Postgres URL secret

- Question: What format should the SUPABASE_DB_URL secret use?
- Answer: Never include the +asyncpg suffix in SUPABASE_DB_URL. That suffix belongs to the app config only; the workflow's psql needs the plain URL.
- Decided by: draft
- Why: Documented in PR #78 and README because psql fails with the suffix. Sources: #78 (merged 2026-07-27), README.md (last changed 2026-10-15).
- Cites: #78, README.md
- Confirmed: no

## D-0030 · 2026-10-22 · Skip api tests needs a reason

- Question: Can I skip the api test step in the pre-commit hook?
- Answer: Skip it with CLIPFARM_SKIP_API_TESTS=1 only with a stated reason in the PR, such as a web-only change or a known blocker, and say so in the PR description.
- Decided by: draft
- Why: The hook's api/tests step was skipped for a web-only PR because of a known CI issue (CF-570, #580). Sources: #587 (merged 2026-09-29).
- Cites: #587
- Confirmed: no
