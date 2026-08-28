# Working in this repo

This file is loaded into every Claude session, so it stays short and covers only
what is easy to get wrong. It does **not** re-describe the project — for that:

| Question | Read |
|---|---|
| What is this, how does the pipeline work, what lives where | `README.md` — Repository Layout, The Processing Pipeline, Key Concepts |
| Why the system is shaped this way | `ARCHITECTURE.md` |
| Running it locally, env vars | `README.md` — Local Development, Configuration |
| Evaluating detection / dead-time changes | `ml/eval/README.md` |
| Deploying | `DEPLOY_RENDER.md` (production, Render), `DEPLOY.md` (backend on a VPS), `DOCKER.md` |
| Running an unattended `/loop` overnight | `docs/overnight/README.md` — it indexes the brief, which is split by phase |

Read the relevant section before changing detection or pipeline code. The
`Key Concepts` section in the README exists specifically to prevent well-meaning
changes that break rally boundaries.

---

## Branch and PR conventions

- **Branches + PRs only, never commit to `main`.** Squash on merge; linear history.
- **Branch name:** `category/CF-##-short-description`. Categories in use: `devops`,
  `ball-detection`, `deadtime`, `eval`, `docs`, `fix`, `ci`, `chore`.
- **PR title:** `type(scope): CF-## description` for new work. Most recent PRs
  follow it, but not all — some older ones open with the card id instead
  (`CF-65a · …`), and the scope is sometimes omitted (`docs: …`). Match the
  documented form rather than the nearest example.
- **PR body must contain a bare `Closes #<issue-number>`.** The template's
  `**Board:** CF-##` line is for humans; GitHub does not parse it, so without a
  real `Closes #N` the issue stays open after merge and its board card never
  moves. One card ≈ one PR.
- Work in a git worktree when juggling several branches; that is the normal flow
  here.

## Before you commit

`.hooks/pre-commit` runs most of what CI runs: `ruff check api/`,
`mypy api/app`, `ruff check ml/eval`, `mypy ml/eval`, `pytest ml/tests`,
`pytest api/tests` (when the dev set is installed — see below), plus eslint,
tsc and vitest for `web/`.

- **Install the tools from `requirements-tooling.txt`**, not `pip install ruff
  mypy pytest`. Those are the pinned versions CI runs (CF-92), so a finding in
  the hook is a finding in CI; an unpinned install can disagree with CI in
  either direction.
- **The hook is not installed by default — wire it up once per clone:**

  ```bash
  git config core.hooksPath .hooks
  ```

  Nothing does this for you, so a clone that skips it commits with no local
  checks at all and only finds out on the PR. `git config core.hooksPath`
  printing nothing means you are in that state.
- **The hook picks its own Python; don't assume `python` works.** It probes
  `python`, then `py`, then `python3`, and uses the first that actually *runs* —
  on Windows the Microsoft Store ships `python`/`python3` stubs that resolve on
  PATH and then fail (CF-259). Whichever it picks, ruff/mypy/pytest must be
  installed into *that* interpreter; the steps invoke them as `-m` modules
  because Python's `Scripts/` directory is often not on PATH.
- **A fresh worktree needs `npm ci` at the repo root first.** Without
  `node_modules`, the hook's eslint/tsc/vitest steps fail or hang, and the failure
  looks like a code problem rather than a missing install. This bites every new
  worktree.
- **`api/tests/` runs in the hook only when the dev set is installed.** CF-102
  closed the CI half of the gap; CF-276 restored the hook step, which had been
  silently skipped since CF-184. Without the install below the step skips
  (loudly, saying why) rather than running — so do the install once per
  machine, or run the suite by hand:

  ```bash
  pip install -r api/requirements-dev.txt   # once — includes the test-only deps
  cd api && python -m pytest tests/   # or `py -m pytest` — see the note above
  ```

  **Install `requirements-dev.txt`, not `requirements.txt`.** Several tests guard
  their imports with `pytest.importorskip`, so without the test-only deps they
  **skip silently** and the run still reports green. Easy to miss, because once
  those packages are on your machine the bare `pytest` command looks fine
  forever — only a fresh clone sees the skips. Test-only packages stay out of
  `requirements.txt` on purpose; that file builds the production image.

## Migrations

Alembic revisions live in `api/alembic/versions/`. The chain is linear, so **two
PRs adding migrations in parallel will collide.** Coordinate the revision order,
and merge before running anything against the shared Supabase instance. See the
Local Development section of the README for the local-db workflow used for schema
work.

The api container migrates on startup **only when `DATABASE_URL` is local**
(`api/scripts/auto_migrate.py`, CF-189) — against Supabase it skips and logs why,
so `docker compose up` cannot advance the shared schema. Applying a migration
there stays a deliberate, separate `alembic upgrade head`.

## Code posture

- **Reporting must never break processing.** Progress writes, metrics, and
  logging are wrapped so a failure degrades the signal rather than killing the
  run. Reviewers cite this consistently — match it in new code.
- Match the surrounding code's idiom, comment density, and naming rather than
  importing a different house style.

## Board

The backlog is a **GitHub Project** (`ClipFarmVB` project #1), not a doc. Cards are
issues titled `CF-## · Description`. `Status` is a single-select
(Todo → In Progress → Code Review → Staging → Done) and `Sprint` is an *iteration*
field — iteration fields are not touched by GitHub's built-in project workflows,
so sprint assignment is always manual.

## Automated review

`.github/workflows/claude-review.yml` posts a Claude review when a PR is opened,
and reads the linked backlog card so it can judge the change against the card's
purpose, deliverables, and scope.

Two behaviours that look like bugs and are not:

- **A PR that edits `claude-review.yml` cannot be reviewed by it.** The action
  requires the workflow file to be byte-identical to the copy on `main` — a guard
  against a PR rewriting the workflow to exfiltrate secrets. Such runs skip.
- **A skipped run still reports `success`.** Do not read a green check as
  "reviewed" without looking, and weigh this before making the review a required
  status check.

To re-review after changes, use `workflow_dispatch` with the PR number. GitHub's
"Re-run jobs" replays the workflow file as it was at the triggering commit, so it
will not pick up later edits.
