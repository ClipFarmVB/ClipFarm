# Step 3 — ticket work

Read on a lap that **starts or receives ticket work**, and whenever a card needs
filing.
The gate list and everything under [Working a ticket](#working-a-ticket) are
`build`-only; [Filing cards](#filing-cards-for-out-of-scope-findings) is reached
in both modes, which is why this file is not.

Part of the unattended-run brief — see [`README.md`](./README.md).

| file | when to read it |
|---|---|
| [`START.md`](./START.md) | once, at the start of a run |
| [`RULES.md`](./RULES.md) | **every iteration** |
| [`REVIEW.md`](./REVIEW.md) | a lap that reviews a PR |
| [`BRIEFS.md`](./BRIEFS.md) | a lap that spawns a round, cold or semi-cold |
| [`FIX.md`](./FIX.md) | a lap that fixes findings |
| [`TICKETS.md`](./TICKETS.md) | a lap that implements a ticket, or a card to file |
| [`REPORTING.md`](./REPORTING.md) | end of the run |
| [`RATIONALE.md`](./RATIONALE.md) | optional background |

---

#### Step 3 — ticket work

**3 — Once every PR that needs a round is in its cycle**, take tickets with the
room [the WIP limit](RULES.md#the-wip-limit-and-areas) has left. Slots go to the
review queue first (CF-564): a PR owed a round and waiting for a slot holds
step 3 back, and a PR already in its cycle does not. *Owed a round*
is [step 1's test](REVIEW.md#step-1--which-prs-need-a-round) and its check-held
clause: a PR reviewed clean and waiting on CI is not a round owed, so it does
not hold step 3 back for the rest of the night.

**This step does not run in `review-only` mode**, and neither does anything under
[Working a ticket](#working-a-ticket). Everything else below still does — in
particular [Filing cards](#filing-cards-for-out-of-scope-findings), because
reviewing is exactly when out-of-scope problems surface, and the 7-card cap
applies in both modes.

### Working a ticket

**Ticket work is dispatched, not done in this session** (CF-563). Each ticket
goes through two subagents in turn — a [planner](#the-planners-brief), then an
[implementer](#the-implementers-brief). This session keeps the one part that
needs to see every ticket at once: whether a plan's files collide with work
already in flight. Several tickets can be at different steps below at the same
time, up to [the WIP limit](RULES.md#the-wip-limit-and-areas).

1. **Claim it.** [Claim](RULES.md#claims) the ticket before anything else, so a
   run cut off mid-ticket leaves it marked rather than silently half-done. The
   claim stays until the ticket's PR is opened, and is released then with
   `PR #<n>` — see step 6.
2. **Dispatch a planner** with [its brief](#the-planners-brief).
3. **Record the plan, then decide.** Write the plan and what its cross-check
   said into the log — including where they disagreed and the planner went its
   own way, and your reasoning if you proceed anyway. If the plan needs a
   decision, or the ticket is too large to implement ([size
   discipline](#size-discipline) below), release it with
   `released: <UTC> — no PR — <why>`. The plan stays in the log.
4. **Check its files against the areas already held** — see [the WIP limit and
   areas](RULES.md#the-wip-limit-and-areas). If any is held, release the ticket
   with `released: <UTC> — no PR — area held by #<n>` and take the next eligible
   one. It can be selected again once `#<n>` is done, and is planned again then:
   `main` will have moved under the first plan.
5. **Dispatch an implementer** with [its brief](#the-implementers-brief), the
   plan as recorded, and a free slot number from 1 to the WIP limit, written at
   the end of its `dispatched:` line as `slot=<k>`. A slot is free again once
   that line has its `finished:`. **Unless the review budget is spent** — then
   step 3 may [only plan and file](RULES.md#the-run-budget), so release the
   ticket with `released: <UTC> — no PR — review budget spent`, the plan kept in
   the log.
6. **When it reports, check GitHub, not the report.** The PR exists, is a
   draft, and GitHub lists the ticket among the issues it closes — read
   `closingIssuesReferences`, not the body, so GitHub's own parse of the
   closing keyword is the one checked:

   ```
   gh pr view <n> --json isDraft,closingIssuesReferences \
     --jq '"draft: \(.isDraft)", "closes: \([.closingIssuesReferences[].number] | join(","))"'
   ```

   Then compare the PR's files with the plan and log every existing file
   outside it — those are the collisions areas could not see. Read the files
   with [the areas command](RULES.md#the-wip-limit-and-areas), not the API's
   list, which names a renamed file by its new path only.

   **Release the ticket's claim** with `released: <UTC> — PR #<n>`. The PR
   carries the work from here, and [a ticket an open PR closes is never
   selected](START.md#choosing-work).

   Then the PR joins step 1, at the front of the queue as a PR this run opened.
   Getting it reviewed — by a cold subagent, never by you — is your job, and
   draft status does not exempt it. **An implementer that reports a failed
   gate** is logged with the step and its output, and its ticket released with
   `released: <UTC> — no PR — gate failed: <step>`.

#### Size discipline

If a ticket would produce a diff too large to review in one
sitting, do not implement it. Write the plan into the log instead — a good plan
beats a half-finished 2000-line PR.

#### The planner's brief

The planner works alone in its own worktree, never sees the rest of the run,
and **changes nothing** — no edits, no branch, no push. Give it the ticket and
`CLAUDE.md`, and [pass what binds it explicitly](RULES.md#dispatching-subagents).
**It starts from `main`, checked out explicitly** —
`git fetch -q origin main && git checkout -q --detach FETCH_HEAD` — because
[its worktree starts wherever this session's checkout
is](RULES.md#dispatching-subagents), and a plan read against another branch
lists the wrong files. Ask it for:

- **A plan** — approach, files, migration if any, tests, and what could go
  wrong — read against the code the ticket touches, not the card alone.
- **The existing files the plan changes**, as repository paths, one per line.
  These become its [area](RULES.md#the-wip-limit-and-areas). Files it will
  create go in a separate list and hold nothing. **A plan that adds an Alembic
  migration lists `api/alembic/versions/` as one path**: two plans that each
  take the next revision number collide at merge with no file in common.
- **A cross-check before it reports.** It spawns its own subagent — with
  worktree isolation, like [every subagent](RULES.md#dispatching-subagents) — to
  review the plan against the actual repository, looking for stale assumptions
  about repo state, a migration number that collides, tests or CI steps that
  already exist, and anything the plan asserts without verifying. It reports
  what the cross-check said, including anything it disagreed with, and what it
  changed in response. The planner rereading its own plan is not a cross-check.
  The cross-check starts from `main` the same way. **Its worktree is the
  planner's to remove**: the harness puts it beside the planner's, at
  `.claude/worktrees/agent-<its id>`, and this session's registry never sees
  that id — so the planner removes it and its `worktree-agent-<its id>` branch
  before reporting, and names its id in the report if it could not.
- **A size verdict**: whether the diff would be reviewable in one sitting.

#### The implementer's brief

The implementer works alone, in its own worktree, and never sees the rest of the
run. Give it the ticket, the plan as recorded after the cross-check, `CLAUDE.md`
and its slot number `k`, and [pass what binds it
explicitly](RULES.md#dispatching-subagents). Ask it to:

1. **Set up its own Python environment before anything installs.** A virtualenv
   in the worktree — `python -m venv .venv`, which `.gitignore` already covers,
   run with a Python that actually works: the pre-commit hook tries `python`,
   `py` and `python3` in that order because a standard Windows install ships a
   `python` stub that runs nothing — whose interpreter runs every `pip` and
   `python` in the gate. Point the
   pre-commit hook at it too: the hook honours `CLIPFARM_PYTHON` before probing
   `PATH`, so set it to `.venv/bin/python`, or `.venv/Scripts/python.exe` on
   Windows.

   Two reasons, and the second holds even with one implementer. **Concurrent
   gates in one interpreter corrupt each other**: the gate installs `numpy`
   after mypy *because numpy's presence changes mypy's error set* (step 3), so
   one implementer's install can land in the middle of another's type check.
   And **on a machine that has run the gate once, numpy is already installed
   before mypy runs**, so in a shared interpreter the order step 3 calls
   load-bearing stops holding after the first gate. A fresh environment is the
   only way that order means anything.

   **What stays shared, and why that is safe.** The compose stack's Postgres
   and Redis: use them, and never restart or recreate the stack, because another
   implementer's gate may be running against it. Sharing Postgres between
   concurrent gates was checked on 2026-09-27: every suite that needs a real
   server creates its own database under a random name or takes advisory locks
   on `uuid4()` keys, and terminates backends only by its own pid or its own
   database. **A test with a fixed database name or a fixed lock key would break
   that**, and make concurrent gates flaky in a way no single run shows.

   **Name the interpreter in every command; do not rely on activating it.** In
   this harness shell state does not carry from one command to the next, so an
   `activate` or an `export` lasts exactly one command, and the gate block below
   — which says plain `pip`, `python`, `ruff`, `mypy` — would quietly fall back
   to the shared interpreter this step exists to avoid. **Write the interpreter's
   absolute path out in full in every command** — the worktree's own path, then
   `.venv/Scripts/python.exe`, or `.venv/bin/python` off Windows. Absolute,
   because the gate `cd`s into `api/`, where a relative `.venv/...` names
   nothing; in full every time, because a variable set in one command is gone by
   the next, exactly like the activation. So `<abs>/python.exe -m pip install ...`,
   `<abs>/python.exe -m ruff check api/`, `cd api && <abs>/python.exe -m pytest
   tests/`, and on the commit itself `CLIPFARM_PYTHON=<abs>/python.exe git commit ...`.

   **Ports**, if it runs the app at all: web on `3100 + k`, api on `8100 + k`.
   The gate itself starts no server.
2. **Implement** on a branch named for the card, cut from the latest
   `origin/main`. **Stay inside the plan.** An existing file the plan does not
   list may still be changed when the work needs it, but it goes in the report
   by name — it is the one collision [areas](RULES.md#the-wip-limit-and-areas)
   could not see coming.

   **If the thing you are writing parses a standard format, use the parser for
   that format.** A version specifier, a requirements line, a semver range, a
   URL, a date — each has a library that already knows every spelling, and the
   repository may have it already: `packaging` is an unconditional requirement
   of pytest, so it is free **in anything the test suite runs**.

   **That scoping is the whole of it — do not carry it into `api/app/` or
   `ml/`.** pytest is not in the production image and no dependency in
   `api/requirements.txt` declares `packaging`, so a production import of it
   passes every local gate and fails in the image `Dockerfile.api` builds. A
   production import needs the line in `requirements.txt`, which is the trap two
   bullets up in the standing rules.

   This is a rule because hand-written patterns lose slowly and expensively. A
   guard added in the run of 2026-08-30 was defeated three times by review, one
   spelling further out each time — `numpy<2` past an `==`-only check, then
   `numpy>=1.26,<2` hiding its cap behind a floor, then `'numpy<2'` in single
   quotes past a double-quote anchor. Each fix looked complete and each was a
   pattern.

   **What the parser bought, precisely.** The fourth version handed the
   *evaluation* to `packaging`, and ~50 further spellings could not defeat that
   half. They did still find four holes — all in the hand-written code
   **around** the parser, one of them a false accept where an extras spec
   (`numpy[extra]==1.26.4`) slipped the name anchor and left an empty specifier
   that admits everything (CF-360). So the rule closes the class it names and
   moves the risk to the glue: mutation-check what feeds the parser, not only
   what it decides.

   **The tell is writing a second or third pattern.** Reaching for another
   regex where one has already failed is the signal to change approach rather
   than refine the expression — and in this case the losing version's own
   docstring already advised taking the dependency, which its author had not
   done.
3. **Run the full gate**, in the environment from step 1. Every step `ci.yml` runs:

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
     tests try the connection themselves and skip on failure — the `pg_locks`
     fixture in `test_worker_safety.py` skips with
     `the lock test database is not reachable` — so they degrade quietly,
     exactly as if no cluster existed; the post-visibility four build their
     database in a fixture with no such guard, and those are what turn the run
     red. Since "do not open a PR if any gate fails" is two lines down, pasting
     a URL your machine cannot reach costs you the PR on a gate that would
     otherwise have been green.

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

   Do not open a PR if any gate fails — report which step failed, with its
   output, and stop.
4. **Push once, at the end, then open a draft PR** following
   `.github/pull_request_template.md`, including the bare `Closes #<issue>` line
   `CLAUDE.md` requires. One push means CI never runs on half the work, and a
   branch that exists is a branch that is finished.
5. **Report**: the PR number; every existing file the PR changes that the plan
   did not list; the gate's summary lines verbatim, skip counts included; and
   the tool versions it ran.

### Filing cards for out-of-scope findings

You will notice real problems that do not belong in the work at hand. File those
rather than fixing them inline or letting them evaporate. A night that files two
cards a maintainer would have written is worth more than one that closes two easy
tickets, and this is the only path by which a run adds work rather than consuming
it — so it gets the same discipline as a PR.

#### What is worth a card

**File** for: a bug, a security issue, a stale comment that would mislead the next
reader, missing coverage on something that matters, a premise no longer true, or
work a review surfaced that is bigger than that PR.

**Do not file** for: vague code smells, style preferences, or anything fixable
inline in under a minute.

**A card is a by-product of work you were already doing.** Do not open files
looking for things to file. A hunt is unbounded, it competes with the priority
order for the night's budget, and what it turns up is exactly the vague-smell
card the line above rejects — you find what is easy to see rather than what
matters. The findings worth filing arrive on their own: out of a review, out of a
gate failure, out of a file you had to read to implement something else.

**A decision can be a card, but it is never only a card.** CF-399 (#520, *Decide
whether learned becomes the condense default*) is the house form, so do not
suppress one — but write it with the options and who decides, and **also put it
in the report's decisions bullet**. A card is a queue nobody reads tonight; the
report is the thing that reaches a human in the morning. The failure is a
judgement call filed and not reported, which looks like progress and is a
finding nobody was told about.

#### The one read both of the next two sections need

Checking for a duplicate and picking the card number are the same read, so take
it once: **every issue in the repository, open and closed, paginated to
exhaustion.** It is the only form that works in every environment this brief runs
in, which is why it is here rather than a search.

```
curl -s "https://api.github.com/repos/ClipFarmVB/ClipFarm/issues\
?state=all&per_page=100&page=<n>"
```

**Paginate until a page comes back empty.** Do not assume a page count — it grows
with every issue filed, so a number written here would be wrong within the week,
the same reason the gate block above records `0 skipped` rather than a passed
total. A capped read returns a maximum that is silently too low, and the card you
file then shares a number with one that already exists.

**Three searches that look cheaper and are not.** Each fails differently, and two
of the three fail *silently*, which is worse than the pagination cost:

- `gh issue list --search` — `gh` routes issue listing through **GraphQL**, which
  a cloud session refuses outright ([`START.md`](START.md#first-establish-what-you-can-actually-do)).
  This one at least fails loudly.
- `GET /search/issues` — works on a maintainer's machine, and is **refused from a
  cloud session by the egress proxy**: *"This GitHub API path is not available:
  sessions are bound to their configured repositories. Use repository-scoped
  endpoints"* (measured 2026-09-14).
- The MCP `search_issues` tool — returned `total_count: 0` for `retention` in the
  same session, against a repository holding three cards with that word in the
  title. **An empty result from a search is not evidence that nothing matches.**

If you do have a working search, use it to narrow the list you already fetched —
not in place of fetching it.

#### First: is it already filed?

Filing a second copy of an existing card is worse than not filing at all: it
costs the triage it was meant to save, and the two drift.

Filter the read above on the noun the finding is *about*, not on your phrasing of
it, and read the two or three nearest in subject rather than stopping at an exact
miss — the existing card was titled by someone who had not seen your finding.
**Filter open and closed together.** Restricting to open cards is the mistake
that makes the second bullet below unreachable: `retention` matches CF-382
(#498, open) and also CF-372 (#467) and CF-194 (#210), both closed, and it is the
closed pair that tells you whether you are looking at a recurrence.

- **An open card already covers it → comment on that card** with your new
  evidence. That is strictly better than filing a second and better than
  skipping: a recurrence is information the original card does not have.
- **A closed card covers it and the problem is back → file, and link it**, saying
  it is a recurrence. "This regressed" and "this is new" want different
  responses, and only the card can carry the difference.

#### Numbering

Title `CF-<n> · <what it is>`, where `<n>` is one past the **highest numeric CF
in the repository, open and closed** — from the read above.

- **Do not infer it from the issue number.** The two drifted long ago and the gap
  is now 121: CF-401 is issue #522 (measured 2026-09-14).
- **Take the numeric part only.** Some cards carry a letter suffix: CF-65a–CF-65f
  are #99–#104, one concern split into phases with a priority each, and CF-65g
  (#149) was added to the set long afterwards. `CF-65g` does not make the next
  card `CF-66`, and the letters are not a closed set you can memorise.
- **Re-derive before every card — do not work the number out once and count up.**
  You are not the only one filing. This has already gone wrong three times:
  **CF-252** is both #273 and #276, **CF-253** both #274 and #277, and **CF-379**
  both #494 and #507 — the last of those being the card behind #508, so the
  mechanism this brief documents has already collided on the brief's own work. A
  clashing card looks fine until someone greps for it.
- **Re-deriving is one page, not the whole set.** A card filed after your first
  read is by construction a newer issue, so refresh with a single newest-first
  page rather than paginating again:
  `…/issues?state=all&sort=created&direction=desc&per_page=100`. Do not reach for
  a search to make it cheaper — see the three above.

If one finding is really several independent pieces of work, either file one card
that says so or use the suffixed form. Either way **each card counts against the
cap below**; splitting is not a way around it.

#### What the card has to contain

Match the house style: what, why it matters, evidence with file and line
references, options where there is a real choice, acceptance. CF-224 (#224) and
CF-239 (#242) are good models.

**The bar is whether someone who was not on this run can act on it without asking
you anything.** Name the files and quote the lines; a card that says a module is
confusing has moved the work rather than recorded it.

**The evidence has to come from a read made for the card** — [the same rule as
everywhere else](RULES.md#evidence-and-the-higher-bar-for-rejecting-a-finding),
and it bites hardest here, because the card outlives the run. The log is
truncated at dawn, so a line number you half-remembered has nothing left behind
it to be checked against by the time anyone opens the card.

**Acceptance is not the optional one.** Say what has to be true for the card to
close. Without it a card cannot be given to a later run at all: `overnight-ok`
means well-specified, and a card with no closing condition is by definition not.

**Say where it came from** — the PR or review that surfaced it. And the other
direction matters more: **if a review declined to fix something because you filed
a card, that review must name the card.** [`FIX.md`](FIX.md) routes every nit
found after the freeze to a card, so this is the common case, not an edge one;
without the link the finding evaporates and the reviewer reads as having dropped
it.

#### Labels, the board, the cap

- Labels from the existing set, including a priority — `P0`, `P1`, `P2` or `P3`.
- **Never put `overnight-ok` on a card you filed.** That label means a human
  judged the ticket safe to implement unattended — it is
  [the selection gate](START.md#choosing-work), and a run that sets it on its own
  card has handed itself work nobody vetted. If you think a card deserves it,
  argue for it in the report, which is the same move the gate already requires
  for an existing issue.
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
- **At the 7-card cap, the findings do not stop — the filing does.** Write the
  rest into the report, one line each, rather than dropping them or filing an
  eighth. The cap exists to keep a night from burying triage, not to make
  findings disappear, and the report is the copy that survives the log.
- Open the body with: `Filed unattended during an overnight run — needs triage.`

### When a command is not available

You may be running in a sandbox rather than on a maintainer's machine. Docker in
particular may be absent, so anything needing `docker compose` — the local stack,
the eval harness — simply cannot run.

**Log that it could not run, and move on. Never report a gate as passing when you
could not execute it.** If a ticket's verification is impossible in the
environment you are in, write the plan and the reason into the log instead of
opening a PR.
