# Working in this repo

This file is loaded into every Claude session, so it stays short and covers only
what is easy to get wrong. It does **not** re-describe the project — for that:

| Question | Read |
|---|---|
| What is this, how does the pipeline work, what lives where | `README.md` — Repository Layout, The Processing Pipeline, Key Concepts |
| Why the system is shaped this way | `ARCHITECTURE.md` |
| Running it locally, env vars | `README.md` — Local Development, Configuration |
| Evaluating detection / dead-time changes | `ml/eval/README.md` |
| Deploying | `DEPLOY.md`, `DOCKER.md` |

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

`.hooks/pre-commit` runs the same checks as CI: `ruff check api/`,
`mypy api/app`, `ruff check ml/eval`, `mypy ml/eval`, `pytest ml/tests`,
plus eslint and tsc for `web/`.

- **A fresh worktree needs `npm ci` at the repo root first.** Without
  `node_modules`, the hook's eslint/tsc steps fail or hang, and the failure looks
  like a code problem rather than a missing install. This bites every new worktree.
- **`api/tests/` is run by neither the hook nor CI** — a known gap (CF-102), not a
  signal that those tests are optional. Run `cd api && python -m pytest tests/`
  by hand when touching anything it covers.

## Migrations

Alembic revisions live in `api/alembic/versions/`. The chain is linear and the
api container runs `alembic upgrade head` on startup, so **two PRs adding
migrations in parallel will collide.** Coordinate the revision order, and merge
before running anything against the shared Supabase instance. See the
Local Development section of the README for the local-db workflow used for schema
work.

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
