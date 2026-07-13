# Contributing to ClipFarm

How we work on this repo. Read this once before your first PR. It applies to
**everyone — human or AI agent.** For *what the project is and how it's built*,
see [README.md](README.md); this doc is only about conventions and workflow.

---

## The workflow in one line

**Branch → commit → PR → CI passes → one review → squash-merge.** Never commit
directly to `main` — it's protected and will reject the push.

---

## Branches

Name every branch:

```
category/CF-##-short-description
```

- **`category`** — lowercase-kebab, from the list below.
- **`CF-##`** — the board card ID this work implements (from the Google Docs kanban).
- **`short-description`** — 2–5 words, lowercase-kebab.

**Examples:** `ball-detection/CF-42-skip-decode` · `devops/CF-40-model-weight-cache` · `docs/CF-22-project-overview`

**Categories** (keep these consistent — pick the closest one):

| Category | For work in |
|---|---|
| `ball-detection` | `ml/pipeline/ball.py` — tracking, contacts, rallies |
| `audio` | `ml/pipeline/audio.py` — energy, cheer scoring |
| `scoring` | `ml/pipeline/score.py` — highlight scoring / ranking |
| `dead-time` | dead-time / condense pipeline |
| `eval` | `ml/eval/` — the model evaluation harness |
| `api` | `api/app/` — FastAPI, models, routers |
| `web` | `web/` — Next.js frontend |
| `devops` | Docker, CI, infra, dependencies, Modal |
| `docs` | Markdown, README, this file |

One card ≈ one branch ≈ one PR. If a card needs 1000+ lines across many files, split it.

---

## Commits

Conventional-commit style, with the card ID in the summary:

```
type(scope): CF-## short description
```

- **`type`** — `feat` · `fix` · `perf` · `refactor` · `chore` · `docs` · `test`
- **`scope`** — the area, usually matching the branch category (e.g. `ball-detection`, `devops`, `web`)

**Examples:**
```
feat(ball-detection): CF-38 rally boundary polish - edge trim + hole bridging
fix(devops): CF-33 pin inference==1.3.3 - unpinned resolver broke RF-DETR
perf(ball-detection): CF-42 skip decode of unused frames via cap.grab()
```

Commit messily while you work (WIP commits, frequent `main` syncs) — squash-merge
collapses it all into one clean commit on `main`, so branch history doesn't matter.
Just make sure the **final squash message** reads like the examples above, not `WIP:`.

---

## Pull requests

- **Title:** same shape as a commit — `type(scope): CF-## description`.
- **Description:** fill in the [PR template](.github/pull_request_template.md) — What,
  Why (+ `Board: CF-##`), How I tested, anything risky.
- **Link the card.** Every PR names its `CF-##` so the board and GitHub stay in sync.
- **Detection / scoring changes:** run the eval harness (`ml/eval/`, CF-55) and paste
  its results row into the PR. "I think this helped" → a number anyone can read.
- **Merge method: squash and merge only.** Check that the auto-filled message is the
  clean PR title, not `WIP: ...`.
- Delete the branch after merge (auto-delete is on).

### What gates a merge (branch protection on `main`)

- **1 approving review** required — you can't approve your own PR, so someone else must.
- **CI must pass:** `Web (lint + typecheck)` and `API (ruff + mypy)`.
- **Force-pushes to `main` are blocked.**
- Your branch may need **"Update branch"** before merging (re-runs CI) if `main` moved.

---

## Before you push: run the checks locally

CI runs the same four checks the pre-commit hook does. Enable the hook once per clone:

```bash
git config core.hooksPath .hooks
```

Now every commit runs **ruff · mypy · eslint · tsc** — the exact CI suite — so you
catch failures before pushing instead of after. Don't bypass it with `--no-verify`;
if a check fails, fix the cause.

---

## Getting write access

Being an **org member is not enough** to push — you need **Write** access on the repo
(org membership defaults to read-only). If "publish branch" fails with a permissions
error, an owner needs to add you: repo → Settings → Collaborators and teams → Write.
Your local commits are safe meanwhile; only the push is blocked.

---

## Gotchas that will bite you

- **Shared database + migrations — the big one.** By default `DATABASE_URL` points at a
  **shared Supabase Postgres** that everyone's dev stack uses. Running an Alembic
  migration from an unmerged branch stamps that shared DB to a revision nobody else
  has, and everyone's `alembic upgrade head` then crash-loops until they pull your
  migration. (This has already broken the app once.) Rules:
  - Do migration/schema work against the **local `db` container**, not Supabase — point
    `DATABASE_URL` at it in `.env.docker` while developing the migration.
  - Merge migrations to `main` **before** they run against the shared DB, in order, and
    give the team a heads-up.
- **Docker env changes don't hot-reload.** `docker compose restart` does *not* re-read
  `.env.docker`. Use `docker compose up -d --force-recreate <service>`.
- **Work in `ClipFarm-org`.** The old `GitProjs\ClipFarm` folder is deprecated — the
  live repo is `ClipFarmVB/ClipFarm`, cloned at `GitProjs\ClipFarm-org`.
- **`web/` is not the Next.js you know** — read [web/AGENTS.md](web/AGENTS.md) before
  touching the frontend.

---

## Code style

Match the file you're editing — its naming, comment density, and idioms. The Python
pipeline leans on thorough docstrings + comments that explain *why* a constant has its
value (see `ml/pipeline/ball.py`); keep that up rather than adding separate design docs.
Both linters (ruff, eslint) are authoritative — if the hook is green, the style is fine.

---

## Where to go deeper

| You want to… | Read |
|---|---|
| Understand the whole system | [README.md](README.md) |
| Change detection / scoring | `ml/pipeline/` (constants + docstrings at the top of each file) |
| Prove a model change helped | `ml/eval/README.md` (the evaluation harness) |
| See the backlog | the Google Docs kanban (CF-## cards) |
