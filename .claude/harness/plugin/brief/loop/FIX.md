# Fix: the fixer, pushing, the settle bar, and unfixable PRs

Read on a lap that **dispatches a fixer, settles a PR, or decides a PR cannot
be fixed**.

Part of the build brief. See [`README.md`](./README.md).

---

## The cycle

1. A **cold** round reviews the PR.
2. A **fixer** acts on the findings and posts a `fix:` reply.
3. A **semi-cold** round gives a verdict on every open finding.
4. Once the open set is empty, a fresh **cold** round decides whether the PR
   settles.

**Never describe a fix inside the review that found it.** No independent
reviewer would then check the fix.

## The fixer's brief

A fixer is dispatched for one job: findings, a human comment, a failing
check, a merge conflict, or a latched retry.

**Its setup:**

- its own worktree, detached at the PR's head, and **`STARTED_AT`: the full
  40-character head SHA** you captured. It verifies `git rev-parse HEAD`
  equals it before editing, and stops if not.
- its own slot (`PORT`, and its own toolchain environment in the worktree)
- the linked ticket, the profile's prose context, and `CLAUDE.md`

**What to hand it for each job:**

- **Findings**: the open set, taken from the round reviews
  (`harness pr N` → `openCriticals`, `openMediums`, `latestReviewId`; read a
  review's body by id with `harness get repos/OWNER/REPO/pulls/N/reviews/ID`),
  plus every human comment since the latest round.
- **A human comment**: the comment, verbatim. The fixer answers it — a change
  if one is asked for, otherwise a reply.
- **A failing check**: `harness failed-log RUN_ID` (the failed jobs, their
  failed steps, and each log's tail).
- **A conflict**: merge `origin/<base>` into the branch and resolve it.
  **Never rebase.** A conflict in generated output — a fixture, a snapshot, a
  lockfile — has no correct hand resolution (git may already have auto-merged
  part of it): regenerate it with its own command and commit the result.

**What it does:**

- **Fix every blocking finding that needs no decision**, and a Medium only
  when genuinely cheap. When fixing a wrong claim, grep for every copy of it.
- **Decline anything that needs a product decision**, saying why.
- **Reject a finding only with evidence** read for the rejection.
- **A finding about the PR body only**: edit the body, read it back to
  confirm, and say so. It spends no round.
- **Run the full gate** (profile `gate`) in its own environment, and quote
  its summary lines, skips included.
- **Every commit subject ends `(round @STARTED7)`** — the first 7 of
  `STARTED_AT`. The loop and the human commit as the same identity, so this
  tag is how a later lap tells the loop's push from a person's.
- **Push once, at the end, with `harness push`** ([pushing](#pushing)), and
  report its outcome word.
- **Post exactly one reply**: `fix: pushed @ SHA7` (the new head) or
  `fix: no push @ SHA7`, then one line per open finding ([fix
  replies](REVIEW.md#what-a-prs-state-is-made-of)). It says what changed,
  without arguing that it is right.

**Rules to pass explicitly:** the list in [dispatching](RULES.md#dispatching).

Before routing again, confirm on GitHub that the reply exists, and the push
if the reply claims one.

## Pushing

Every push to a PR branch, by a fixer or an implementer updating its own PR:

```
node PLUGIN/bin/harness.mjs push --branch BRANCH --started STARTED_AT
```

It pushes only if the remote branch is still exactly `STARTED_AT`, read with
`git ls-remote`. **A plain push is not enough**: it rejects commits added on
top, but *accepts* a rewind — the fix still descends from the rewound tip —
and silently restores a commit someone removed. Never `--force-with-lease`; it
is a force flag. It prints one outcome:

| outcome | what happened | next |
|---|---|---|
| `pushed` | the fix landed | post `fix: pushed @ SHA7`; the orchestrator then runs `harness synced ID --cwd WORKTREE` for a ticket's PR |
| `head moved` | someone pushed or rewrote the branch | push nothing. Never rebase, merge or force. Report it; the orchestrator applies `unsettled: head moved` |
| `remote unreadable` | the push never reached the remote | report it; route again later |
| `push refused` | a protection, hook or the harness refused it | [pin the fix](#latched), report git's error verbatim; `unsettled: latched` |

### Latched

After a refused push the fix exists only on a detached HEAD. **Pin it before
anything touches the worktree**: `git -C WORKTREE branch
harness-latched-PR-STARTED7`. Name the pin and the full `STARTED_AT` in the
`unsettled: latched` comment. The one allowed retry, after a human clears the
state, is a push-only fixer: detach at the pin, confirm `git merge-base
--is-ancestor STARTED_AT PIN`, and `harness push` with the same `STARTED_AT`.
No new commits, no gate. Delete the pin only once the PR head equals it. Pins
are machine-local; a run elsewhere says it cannot retry.

### When the head moved

When a fixer reported `head moved`, or went silent and the head changed:

```
node PLUGIN/bin/harness.mjs moved --pr N --started STARTED_AT
```

- **`our push landed`**: every new commit carries this fixer's round tag.
  Post the missing `fix:` reply (unless one exists) and spawn the semi-cold
  round.
- **`person pushed`** or **`rewritten`**: `unsettled: head moved`, naming it.
  The next commits clear it.
- **`unchanged`**: re-dispatch.

A lost or rate-limited fixer is never simply re-dispatched without this check.

## The settle bar

Apply the settled state (profile `settled label` with labels; the
`settled: @ SHA7` comment either way) only when **all** of these hold at the
current head. `harness pr N` → `settleBar` evaluates them:

- **The latest round is a cold round at this head**, and it raised nothing
  blocking. `cold: findings` with only Mediums qualifies (unless profile
  `mediums block: yes`).
- **The open set is empty.** Open Mediums are first recorded on the PR in one
  `loop: known limitations @ SHA7` comment.
- **Under profile `settle rounds: 2`**, a PR that never had a finding has two
  clean cold rounds at this head.
- **The checks pass** ([checks](REVIEW.md#checks)).
- **No human comment is unanswered.**
- **Every profile `ship reviewers` verdict at this head is SHIP**
  ([ship reviewers](BRIEFS.md#ship-reviewers)), asked for only once all the
  above hold.
- **Only cold verdicts earn it.** Never this session's, never a semi-cold
  round's.

Before posting `settled:`, re-verify any claim in the PR body about things
outside the diff; they go stale while a PR waits.

## Freeze the head once nothing is open

**Once the open set is empty, only a new blocking finding, a failing check, a
conflict or a human request may change the head.** A Medium or nit fix would
earn another cold round, which can find another Medium or nit. A converged PR
can cycle that way until the ceiling stops it.

## When a PR cannot be fixed

Push everything that can be fixed first. Then apply `unsettled` with the
[reason](REVIEW.md#terminal-states) that fits, and post its record comment:

- **`needs a decision`**: each open finding with its options and a
  recommendation, and what the human does next — answer in a comment, then
  remove the label (or, with `labels: no`, just answer).
- **`latched`**: what refused the push, verbatim, the pin, and `STARTED_AT`.
- **`head moved`**: what `harness moved` found.
- **`ran out of rounds`**: which cap — the ceiling, the fix cap, the check
  cap, or the budget — and for a check, its name and conclusion.

**An `unsettled` PR is parked**: no rounds, no fixers, no merges from the
base, until a human acts or new commits arrive. The triager decides whether
to re-plan its ticket or close the PR.
