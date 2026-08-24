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
- **You are never the reviewer.** Every PR still gets reviewed — by a cold
  subagent, spawned per step 1, whether or not this session wrote the diff.
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
label was applied. That label is the record that a cold round cleared the bar
below — no Critical and no Medium finding, nits permitted. Without it, "already
reviewed" has to be inferred from timestamps, and a PR abandoned mid-cycle looks
identical to one reviewed clean.

**Skip PRs labelled `unsettled`** unless commits have landed since the label was
applied — the same carve-out `review-settled` gets, and for the same reason: a
PR nobody can push a fix to and a PR that has since been fixed must not look
alike. The label is the opposite record to `review-settled`: Critical or Medium
findings are open and a human is wanted. New commits make it eligible again with
its round count reset; without them it stays skipped until a human removes the
label.

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

What "cold" withholds is **how the diff came to be**: the plan, the reasoning,
the summary of what was built, and what any earlier round found. Give the
subagent the PR number and let it read the repository itself. Re-deriving that
context from cold is what a subagent normally costs you; here that cost is the
point. Step 2 of [Working a ticket](#working-a-ticket) already spawns one to
cross-check plans — same mechanism, pointed at a diff.

**It is cold to this session, not to the PR.** Given a number it will read the
body you wrote and every thread on it, including the "what changed" replies step
2 requires — so your framing reaches it anyway, through GitHub rather than
through the prompt. That leak is not fully closable while reviews happen on a
PR. Narrow it: write PR bodies and thread replies to state *what changed*, not
to argue the change is right. A body that pre-empts objections anchors every
reviewer who ever reads it, cold or not.

What "cold" does **not** withhold is how to do the job. Give it the review
instructions below in full, plus the rules that bind it as an agent: **no
attribution stamps** on its comment, never push, never merge, never deploy,
never echo a credential. Pass those rules explicitly — do **not** hand it the
[Hard rules](#hard-rules) section wholesale, which would tell the reviewer you
just spawned that it is never the reviewer. A subagent in a sandbox inherits
none of your local settings, so the no-stamp rule has to travel with it or its
review arrives signed.

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
a separate review of the new head.

That second review is a **full review of the PR as it now stands**, not a
verification of your fix — it cannot check that the fix holds, because it is not
told a fix happened. That is the trade: you give up a targeted check on the
patch and get an unanchored pass over the whole diff instead.

**So a later round's silence is not evidence.** A round that does not re-raise a
finding is exactly what you would expect from a reviewer that found *different*
things this time — which the closing paragraph says is the norm. Closing a
finding is **your** job, not a reviewer's: check your own fix against what the
finding actually said, and reply on the thread with the commit that does it.
That is a fair use of this session — verifying a specific claim against specific
lines is the one thing anchoring does not spoil.

**Never describe a fix inside the review that found it.** Two of the five PRs in
the second run did exactly that — found something, fixed it, and posted one
review narrating both. Nothing independently looked at the fix, and because the
fix commit predated the review, step 1 saw no commits after it and skipped the
PR forever. The next round is not ceremony: it is a fresh reviewer over code
that changed *after* the previous one formed its judgement.

**Cycle until a cold round produces no Critical and no Medium finding.** Not a
fixed number of rounds: each round is a new subagent reading the diff without
the last one's conclusions, so it is a genuinely different pass, and stopping at
two stops while that is still paying. Nits may remain — requiring zero findings
would review forever.

**When a cold round clears that bar, label the PR `review-settled`.** That is
the terminal state, and it is what stops future runs re-reviewing finished work.

**The bar spans every round, not just the last one.** Settle only when the most
recent cold round raised no Critical or Medium finding **and** every Critical
and Medium from earlier rounds has a fix you can point at — a commit, and a
reply saying so. A quiet round on top of an unfixed Critical is a reviewer
looking elsewhere, and treating it as the terminal state buries the finding
under a label that stops anyone looking again.

**Only a cold reviewer's verdict earns the label.** This session labelling its
own work settled is the anchored judgement stamped final.

**Label last.** Fix or drop the remaining nits *before* applying it. A commit
after the label lands re-opens the PR for review under the rule above, which is
the opposite of what the label is for.

**A finding you cannot fix stops the *cycling*, not the work.** If a Critical or
Medium needs a human decision, or sits on a branch you may not push to, first
fix everything else that round raised and push it — those findings are real and
abandoning them wastes the round that found them. *Then* label the PR
`unsettled`, record what is left in the log and the report, and move on. Do not
spend further rounds on it: a new round is cold to your reasoning but not to the
code, so it re-derives the same blocked finding off the same unchanged lines.

**Ceiling: four cold rounds per PR per run**, so a pathological PR cannot
consume the whole night. Hitting it is the same outcome: fix what you can,
label `unsettled`, record, move on.

**Two passes, in this order.**

*First pass — breadth.* Give every PR that has no review at all one cold round.
At twenty open PRs that is twenty reviews, and it is the highest-value spend in
the run: a PR nobody has looked at once benefits more from a first pass than a
reviewed PR does from a fourth.

*Second pass — depth.* Then pick **at most three PRs** to carry through the
fix-and-re-review cycle, preferring ones this run opened, since those are the
ones you may push to. Cycle each to the clean bar or the four-round ceiling.

**Run budget: 32 cold reviews**, which covers both passes with room over. The
ceiling alone would permit eighty — a whole night of nothing but reviewing,
which together with "stop on usage limits" means step 3 never happens. Note that
the first pass deliberately eats most of a night when the queue is this deep;
that is a real outcome to report, not a failure.

If the budget runs out with findings open on a PR, it gets the same treatment as
the ceiling: `unsettled`, recorded, move on. Never leave a PR with open findings
carrying no label — unlabelled and unreviewed are indistinguishable to the next
run, which is the whole reason these labels exist.

**Log every round as you finish it** — `PR #<n> — cold round <k>/4, budget
<used>/32` plus the tiers found. Neither bound is enforceable unless the count
survives: context may be compacted mid-run, and counts you hold in your head
reset to zero when it is. Recover both from the log at the start of every
iteration, and cross-check the per-PR count against the review comments already
on the PR.

None of this proves a PR clean. A fresh subagent is unanchored but still the
same model with the same priors, so a new round is a different pass, not an
independent one: it finds *different* things, not *all* things, and the returns
diminish across rounds without reaching zero. That is exactly why there is a
ceiling and not just a clean bar. This cuts how many rounds a human has to run
by hand; it does not answer when a PR is actually done.

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
- PRs labelled `unsettled`, why — human decision needed, branch you cannot push
  to, four-round ceiling, or review budget exhausted — and what is still
  outstanding on each
- Whether the review budget ran out, and how many PRs never got a first round
- PRs opened, with card and branch — and **what to test to verify each one**
- Cards filed, one sentence each on why
- Tickets attempted but abandoned, and why
- Every decision needing a human call
- Anything that failed, **verbatim** — do not summarise errors away

Be honest. A report that overstates what landed is worse than a short one.
