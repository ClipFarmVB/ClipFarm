# Unattended run brief

Instructions for an agent running `/loop` with nobody watching — overnight, or
any stretch where questions cannot be answered.

Start one with:

```
/loop Read OVERNIGHT_RUN.md and follow it exactly. Re-read .claude/overnight-log.md first each iteration so you do not repeat work.
```

The log stays gitignored on purpose: it is scratch memory for one run. The report
that has to survive is posted as an issue — see [Reporting](#reporting).

> **Update "This run" before starting.** Everything under
> [Standing policy](#standing-policy) holds every time. The section immediately
> below does not, and an agent given a stale scope will work confidently on the
> wrong things. This repository has been bitten by exactly that: CF-192's
> worst-case reasoning was invalidated by CF-224 without the text changing, and
> CF-224 read as "fixed" while production was still failing.

---

## This run

**Last updated: 2026-08-24.** If that date is not recent, stop and ask before
running.

### In scope

Work issues carrying the **`overnight-ok`** label:

```
gh issue list --state open --label overnight-ok --json number,title,labels
```

That label means a human has judged the ticket safe to implement unattended —
well-specified, no design or legal decision, no production data, no credentials.
It is the selection gate. **Do not take an issue that does not carry it**, however
appealing it looks; if you think one deserves it, argue for it in the report
instead of taking it.

Work highest priority first (`P0` > `P1` > `P2` > unlabelled). One ticket per
iteration. If a ticket turns out to need a decision after all, say so in the log,
drop it, and move on — do not guess.

If nothing carries the label, or everything that does is done, **stop the loop**.

### Environment notes for this run

- Render is **suspended** at $0/month. Starting it costs money.
- `claude-review.yml` is **disabled**, so nothing reviews PRs automatically.

---

## Standing policy

### First: establish what you can actually do

Before relying on any capability, check it, and record the result in the log in
one block. The first run discovered three gaps separately, mid-work.

- **Projects v2** — `gh project item-list 1 --owner ClipFarmVB --format json`.
  Needs the `project` token scope, which is often absent. This does **not** gate
  any work: selection is by label, which plain repo scope reads fine. It only
  affects board hygiene — without it you cannot remove the report issue from the
  project. Note the gap in the report and carry on.
- **Docker** — `docker info`. If absent, the local stack and the eval harness
  cannot run at all.
- **Gate tool versions** — read the versions `ci.yml` installs and compare with
  what is installed here. See the gate step below.

State every gap in the report. A capability you assumed and did not have is the
most expensive kind of surprise in an unattended run.

### Hard rules

- **Never** push to `main`, merge a PR, or force-push anything.
- **Only push to branches this run created.** Pushing to an existing PR's branch
  needs prior sign-off — the harness requires permission and the brief must not
  contradict it. If a fix belongs on someone else's branch, describe it in a
  review comment instead.
- **Never** deploy, unsuspend a hosting service, or touch production
  infrastructure.
- **Never** run the local stack against a `DATABASE_URL` pointing at Supabase.
  Confirm `.env.docker` names the local `db` container first.
- **Never** read, echo, or commit `.env.docker` or any credential.
- Every PR opens as a **draft**. It is work nobody has vetted yet, and draft is
  the honest signal for that. Your PRs still get reviewed (step 1) — by a cold
  subagent, never by you — and a draft reviews and takes pushes exactly like any
  other. You never merge, never deploy.
- **Never review a diff this session wrote.** Reviews are spawned cold; see
  step 1.
- **Maximum 5 new PRs** and **6 new cards** per run.
- **No attribution stamps.** Do not add "Generated with Claude Code", a
  `Co-Authored-By` trailer, a session link, or any similar footer to commits, PR
  bodies, review comments, or issues. Local settings suppress these but a sandbox
  does not inherit them, so this is on you.
- If a command fails because of usage limits, **stop the loop** — do not retry.
- If nothing in scope is actionable, **stop the loop**. A run that reviews two
  PRs and opens nothing is a fine outcome.

### Log before you finish each iteration

Append a dated section to `.claude/overnight-log.md`: what you did, what you
decided and why, and anything needing a human call. **Read it at the start of
every iteration.** Context may be compacted between iterations; the log is the
only thing that survives.

### Priority order

Finish work already in flight before starting anything new.

**1 — Review open PRs that need one.** A PR needs a review if it has **no
review at all** — including one you opened earlier in this run — or if it has
commits since its most recent review.

**Skip PRs labelled `review-settled`** unless commits have landed since the
label was applied. That label is the record that a cold review round found
nothing actionable; without it, "already reviewed" has to be inferred from
timestamps, and a PR abandoned mid-cycle looks identical to one reviewed clean.

Compare each PR's head commit against the commit of its most recent review. If
there are commits since, it needs another round.

**Never review from this session. Spawn a subagent and let it review cold.**
The session that wrote the code is the most anchored possible reviewer: once it
has judged a file fine it checks the delta rather than re-deriving that
judgement, so everything already blessed becomes invisible. A long context also
spends attention on conversation history that a cold reviewer spends entirely on
the diff. Clearing the context is not a retry — it produces a *different
reviewer*, which is why a fresh pass keeps finding real things after both sides
agreed a PR was ready. In this loop one session writes the code, opens the PR,
reviews it and fixes it; nothing in that chain is cold unless you make it so.

Hand the subagent the PR number and nothing else. Not the plan, not the
reasoning, not a summary of what was built, not what an earlier round found. It
reads the repository itself. Re-deriving context from cold is what a subagent
normally costs you; here that cost is the point. Step 2 of
[Working a ticket](#working-a-ticket) already spawns one to cross-check plans —
same mechanism, pointed at a diff.

Its brief: run `/code-review` on that PR and post the findings as one review
comment, in tiers:

- **Critical** — correctness, security, data loss
- **Medium** — should fix before merge
- **Nit** — style, naming, comments

Challenge the design where warranted, not only the code; say so when a premise
looks wrong. **Verify claims against the repository** rather than trusting the PR
description — that has caught real errors here more than once. Never mark a
finding confirmed without checking it.

Do not use `/code-review ultra`; it is billed separately and user-triggered.

**2 — Address review findings on PRs you own** (`gh api user -q .login`, which
includes every PR you opened during this run). If a fix needs no human decision,
implement it, push to that PR's branch, and reply on the thread saying what
changed. If it needs a judgement call, log it and leave it.

**This is a cycle, and the order matters.** A cold subagent posts the review.
*Then* you push the fix. *Then* a **new** subagent, spawned just as cold, posts
a separate re-review against the new head — confirming the fix holds and
introduced nothing new.

**Never describe a fix inside the review that found it.** Two of the five PRs in
the second run did exactly that — found something, fixed it, and posted one
review narrating both. Nothing independently looked at the fix, and because the
fix commit predated the review, step 1 saw no commits after it and skipped the
PR forever. The next round is not ceremony: it is a fresh reviewer over code
that changed *after* the previous one formed its judgement.

**Cycle until a cold reviewer produces no Critical and no Medium finding.** Not
for a fixed number of rounds — every round is a new subagent, so every round is
a different reviewer, and stopping at two just stops early. Nits may remain;
requiring zero findings would review forever.

**When a cold round clears that bar, label the PR `review-settled`.** That is
the terminal state, and it is what stops future runs re-reviewing finished work.

**Only a cold reviewer's verdict earns the label.** This session labelling its
own work settled is the anchored judgement stamped final, and that label is
precisely what stops anyone looking again. Do not apply it while Critical or
Medium findings are outstanding — including findings deferred to a human. If a
human removes it, the PR wants another look.

**Ceiling: four cold rounds per PR per run**, so a pathological PR cannot
consume the whole night. If Critical or Medium findings remain when the ceiling
is hit, leave the PR unlabelled and record it as **unsettled**, with the
outstanding findings, in both the log and the report.

None of this proves a PR clean. A fresh subagent is cold but still the same
model with the same priors: it finds *different* things, not *all* things. The
returns across repeated clears diminish without reaching zero. This cuts how
many rounds a human has to run by hand; it does not answer when a PR is
actually done.

**3 — Only when 1 and 2 are clear**, take one ticket from "This run".

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
   ruff check api/
   mypy api/app --ignore-missing-imports
   ruff check ml/eval
   mypy ml/eval --ignore-missing-imports --explicit-package-bases --namespace-packages
   python -m pytest ml/tests/
   cd api && python -m pytest tests/
   ```

   For web changes, all three — the test step is easy to forget:

   ```
   npm run lint --workspace=web
   npm run typecheck --workspace=web
   npm run test --workspace=web
   ```

   **On tool versions.** `ci.yml` installs `ruff mypy pytest` **unpinned**, so CI
   resolves whatever is latest on the day it runs. That means there is no pinned
   version to match — the closest you can get is installing the same unpinned way
   CI does, which the environment's setup script now handles. Do not claim a gate
   matches CI's tooling; state the versions you actually ran in the report.

   This is not a detail. The first run's reviews were checked with different
   `ruff`/`mypy` than CI used, which the agent flagged against its own work —
   CF-92 (#255) exists to pin them. **Once that lands, match the pinned versions
   and drop this caveat.**

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
- Add to the `ClipFarm Backlog` project with the **Sprint field left unset** —
  these are for triage, not for silently joining the current sprint.
- Open the body with: `Filed unattended during an overnight run — needs triage.`

### When a command is not available

You may be running in a sandbox rather than on a maintainer's machine. Docker in
particular may be absent, so anything needing `docker compose` — the local stack,
the eval harness — simply cannot run.

**Log that it could not run, and move on. Never report a gate as passing when you
could not execute it.** If a ticket's verification is impossible in the
environment you are in, write the plan and the reason into the log instead of
opening a PR.

### Repo traps that have already cost time

- Migration numbers collide. Check `api/alembic/versions/` for the current head;
  never assume.
- `api/tests/` exists and CI runs it. Test-only dependencies go in
  `api/requirements-dev.txt`, never `requirements.txt` — that file builds the
  production image.
- CF numbers have drifted from issue numbers. Check the highest existing `CF-`
  number; do not infer it from the issue count.
- A closed issue may be `COMPLETED` or `NOT_PLANNED` — opposite facts behind the
  same `state`. Always read `stateReason`.

### Reporting

Post the run report as a GitHub issue titled `Overnight run — <YYYY-MM-DD>`,
labelled `chore`, added to the project with the Sprint field unset. A log file in
a sandbox is a report nobody reads; the issue is the copy that arrives.

Put the same summary at the top of `.claude/overnight-log.md`.

The report contains:

- PRs reviewed, how many cold rounds each took, and findings by tier
- PRs left **unsettled** at the round ceiling, with what is still outstanding
- PRs opened, with card and branch — and **what to test to verify each one**
- Cards filed, one sentence each on why
- Tickets attempted but abandoned, and why
- Every decision needing a human call
- Anything that failed, **verbatim** — do not summarise errors away

Be honest. A report that overstates what landed is worse than a short one.
