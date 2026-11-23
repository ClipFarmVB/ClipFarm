# Harness profile — ClipFarmVB/ClipFarm

The team repo. This encodes what `docs/overnight/` does today: review own PRs,
never merge, two clean rounds for a PR that never had a finding, Mediums
block, file-level areas, a 40-round budget. Change a line here to change the
run.

```harness
repo: ClipFarmVB/ClipFarm
tracker: github
labels: yes
pr scope: own               # every open PR this account opened; never widen
ready label: overnight-ok
claim label: in-progress
hold label: hold
decision label: needs-decision
settled label: review-settled
unsettled label: unsettled
merge policy: human         # the loop never merges; humans merge
wip limit: 6
round cap: 7
round budget: 40
pr cap: 6
settle rounds: 2
mediums block: yes          # ClipFarm's current bar; `no` is what HTN/Solo learned (7 rounds on one PR)
areas: files
serial dirs: api/alembic/versions/
plan step: on
self-approve cards: no      # the run never puts overnight-ok on its own cards
gate: see "The gate" below
branch: category/CF-NUM-desc
ticket line: Closes #NUM
harness paths: docs/overnight/, .hooks/, api/tests/test_overnight_brief.py, api/tests/test_overnight_push_guard.py
port base: 3100
status: issue
```

## The product

ClipFarm: video clipping for volleyball (ball detection, scoring, dead-time
removal). `api/` (FastAPI, Python), `web/` (Next.js), `ml/` (models, eval).

## What is valuable here

The board is a GitHub Project (`ClipFarmVB` #1). Cards are `CF-##`; a PR body
carries a bare `Closes #N` line **and** `**Board:** CF-##` — "CF-53" alone
closes nothing. PR titles are `type(scope): CF-## …`. Squash merges.

## The gate

CI (`.github/workflows/ci.yml`) is the gate; mirror it per worktree:

- web: `npm ci --workspace=web`, `npm run lint|typecheck|test --workspace=web`
- api: `pip install -r requirements-tooling.txt`, `ruff check api/`,
  `mypy api/app --ignore-missing-imports`, `pip install -r
  api/requirements-dev.txt`, `python -m pytest tests/` (from `api/`)
- ml: `ruff check ml/`, `mypy ml/eval --ignore-missing-imports
  --explicit-package-bases --namespace-packages`, `pip install
  "numpy==1.26.4"` (after mypy), `python -m pytest ml/tests/`

Each implementer creates `.venv` in its own worktree and writes its absolute
interpreter path (`.venv/Scripts/python.exe` on Windows) into every command.
Tool versions are pinned in `requirements-tooling.txt`; report the ones run.

**Silent skips:** the lock-safety tests skip without Postgres at
`localhost:5432` (or `LOCK_TEST_DATABASE_URL`). A green gate with those skips
is short by that many tests; say so.

## Traps

- Alembic migrations collide in parallel: two with the same down-revision
  merge cleanly and leave two heads. `serial dirs` holds the directory whole.
- `.env.docker` holds real credentials; never read or print it.
- Render deploys are suspended; the `claude-review.yml` workflow is disabled.
- The pre-commit hook is opt-in (`git config core.hooksPath .hooks`).
