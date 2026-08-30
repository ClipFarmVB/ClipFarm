# Step 3 — ticket work

Read on a lap that **implements a ticket**, and whenever a card needs filing.
The gate list and everything under [Working a ticket](#working-a-ticket) are
`build`-only; [Filing cards](#filing-cards-for-out-of-scope-findings) is reached
in both modes, which is why this file is not.

Part of the unattended-run brief — see [`README.md`](./README.md).

| file | when to read it |
|---|---|
| [`START.md`](./START.md) | once, at the start of a run |
| [`RULES.md`](./RULES.md) | **every iteration** |
| [`REVIEW.md`](./REVIEW.md) | a lap that reviews a PR |
| [`FIX.md`](./FIX.md) | a lap that fixes findings |
| [`TICKETS.md`](./TICKETS.md) | a lap that implements a ticket, or a card to file |
| [`REPORTING.md`](./REPORTING.md) | end of the run |
| [`RATIONALE.md`](./RATIONALE.md) | optional background |

---

#### Step 3 — ticket work

**3 — Only when 1 and 2 are clear**, take one ticket from "This run".

**This step does not run in `review-only` mode**, and neither does anything under
[Working a ticket](#working-a-ticket). Everything else below still does — in
particular [Filing cards](#filing-cards-for-out-of-scope-findings), because
reviewing is exactly when out-of-scope problems surface, and the 6-card cap
applies in both modes.

### Working a ticket

1. **Plan first.** Read the card and the code it touches. Write the plan into the
   log: approach, files, migration if any, tests, and what could go wrong.
2. **Cross-check the plan before implementing.** Spawn a subagent to review it
   against the actual repository, looking for stale assumptions about repo state,
   a migration number that collides, tests or CI steps that already exist, and
   anything the plan asserts without verifying. Record what it said — including
   when it disagreed and you proceeded anyway, with your reasoning.
3. **Implement** on a branch named for the card.
4. **Run the full gate.** Every step `ci.yml` runs:

   ```
   pip install -r requirements-tooling.txt
   ruff check api/
   mypy api/app --ignore-missing-imports
   ruff check ml/                       # ml/ entire, not ml/eval (CF-254)
   mypy ml/eval --ignore-missing-imports --explicit-package-bases --namespace-packages
   pip install "numpy==1.26.4"          # AFTER the lint/type steps — see below
   python -m pytest ml/tests/
   pip install -r api/requirements-dev.txt
   cd api && python -m pytest tests/          # see LOCK_TEST_DATABASE_URL below
   ```

   **Set `LOCK_TEST_DATABASE_URL` only if your Postgres is not on
   `localhost:5432`** — and check the skip count either way. `api/tests/_pg.py`
   probes two hardcoded `localhost:5432` candidates *and takes a set value
   verbatim, unprobed*, so the variable has two opposite failure modes:

   - **Unset, no local cluster** — the CF-184 advisory-lock suite and the
     post-visibility pg tests **skip**: **8 skipped, exit 0** — green, eight
     tests short. This is the silent-skip failure the paragraph below warns
     about, reached through the environment rather than through a missing
     package.
   - **Set but unreachable** — a hard `psycopg2.OperationalError` and a non-zero
     exit: **4 skipped, 4 errors**. The split is not arbitrary and is worth
     knowing, because only half of this state announces itself: the CF-184 lock
     tests try the connection themselves and skip on failure
     (`test_worker_safety.py:538`), so they degrade quietly exactly as if no
     cluster existed; the post-visibility four build their database in a
     fixture with no such guard, and those are what turn the run red. Since "do
     not open a PR if any gate fails" is two lines down, pasting a URL your
     machine cannot reach costs you the PR on a gate that would otherwise have
     been green.

   With a reachable cluster: **0 skipped**. CI's value is
   `postgresql://postgres:postgres@localhost:5432/postgres`; point the variable
   at whatever local cluster you actually have, or leave it unset and let the
   probe find one.

   **Read the skip count, not the passed count.** Those three states are told
   apart by the skips and errors alone, and that is the whole reason this block
   exists — a silent skip is invisible in an exit code. The passed total is
   deliberately not recorded here: it moves with every merge that adds a test,
   so a number written into this file is wrong by the next one and cannot be
   told apart from the failure it is meant to signal. `0 skipped` is the state
   to be in; `8` means you are eight tests short of having run the gate.

   **The three installs are part of the list and their order is load-bearing.**
   `numpy` goes in after ruff and mypy on purpose: both are tuned against a
   dependency-free environment and numpy ships `py.typed`, so its presence
   changes what `--ignore-missing-imports` resolves and hence the error set mypy
   reports. `api/requirements-dev.txt` goes in before the api suite because
   several tests guard their imports with `pytest.importorskip` and **skip
   silently** without it — a green run that checked nothing.

   Note `ruff` widened to all of `ml/` while `mypy` is still `ml/eval` only.
   That asymmetry is deliberate and lives in `ci.yml`'s own comment; do not
   "fix" it by widening mypy to match.

   For web changes, all four — the test step is easy to forget:

   ```
   npm ci --workspace=web
   npm run lint --workspace=web
   npm run typecheck --workspace=web
   npm run test --workspace=web
   ```

   **On tool versions.** `ci.yml` installs `requirements-tooling.txt`, which is
   **pinned**, so there is a version to match and you should match it — install
   that file rather than `pip install ruff mypy pytest`, and a finding in your
   gate is a finding in CI. State the versions you actually ran in the report
   anyway; that is cheap and it is what catches a drift between the file and the
   environment.

   This mattered: the first run's reviews were checked with different
   `ruff`/`mypy` than CI used, and ruff 0.16.0 widened its default rule set and
   reddened a PR in untouched code. CF-92 (#255) pinned them and has since
   landed, which is why this paragraph no longer says the opposite.

   Do not open a PR if any gate fails — log it and move on.
5. **Open a draft PR** following `.github/pull_request_template.md`, including
   the bare `Closes #<issue>` line `CLAUDE.md` requires. Then go back to step 1:
   a PR you just opened has no review yet, and getting it reviewed — by a cold
   subagent, never by you — is your job. Draft status does not exempt it.

**Size discipline.** If a ticket would produce a diff too large to review in one
sitting, do not implement it. Write the plan into the log instead — a good plan
beats a half-finished 2000-line PR.

### Filing cards for out-of-scope findings

You will notice real problems that do not belong in the work at hand. File those
rather than fixing them inline or letting them evaporate.

**File** for: a bug, a security issue, a stale comment that would mislead the next
reader, missing coverage on something that matters, a premise no longer true, or
work a review surfaced that is bigger than that PR.

**Do not file** for: vague code smells, style preferences, or anything fixable
inline in under a minute.

- Title `CF-<n> · <what it is>`. Get `<n>` from the **highest existing CF number**
  across all issues — it has drifted from the issue numbers, so do not infer it.
- Match the house style of existing cards: what, why it matters, evidence with
  file and line references, options where there is a real choice, acceptance.
  CF-224 (#224) and CF-239 (#242) are good models.
- Labels from the existing set, including a priority.
- **Do not try to add the card to the `ClipFarm Backlog` project — a project
  workflow adds new issues automatically, with `Status: Todo`.** Verified: every
  card the first unattended run filed reached the board this way, without the
  `project` scope. Nor is there anything to do about **Sprint**: iteration
  fields are untouched by GitHub's built-in workflows, so it starts unset, which
  is what these cards want — they are for triage, not for silently joining the
  current sprint.

  So **do not report cards as missing from the board.** Every run so far has
  reported that, and it has been wrong each time. If you want to check rather
  than assume, `gh project item-list` reads with `project` scope; if you do not
  have it, say the board was unverified rather than saying the cards are off it.
- Open the body with: `Filed unattended during an overnight run — needs triage.`

### When a command is not available

You may be running in a sandbox rather than on a maintainer's machine. Docker in
particular may be absent, so anything needing `docker compose` — the local stack,
the eval harness — simply cannot run.

**Log that it could not run, and move on. Never report a gate as passing when you
could not execute it.** If a ticket's verification is impossible in the
environment you are in, write the plan and the reason into the log instead of
opening a PR.
