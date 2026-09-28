# Tickets: choosing, claiming, planning, implementing

Read on a lap that **dispatches a planner or an implementer**. Where tickets
come from and how they are claimed is the tracker's business: read
`brief/trackers/<profile tracker>.md` alongside this.

Part of the build brief. See [`README.md`](./README.md).

---

## Choosing tickets

`harness tickets` answers for every tracker, with three lists:

- **`eligible`**, in the order to take it. **Never reorder it.** What makes a
  ticket eligible is the tracker's business (the tracker file); for GitHub:
  open, carrying profile `ready label`, none of the claim, decision or hold
  labels, no open PR already closing it, every `Depends on #n` closed as
  `completed`.
- **`resumable`**: work this account already holds (a live session with its
  branch). Resume it in its worktree before starting anything new; it counts
  toward the WIP limit.
- **`skipped`**, each with why. A pack may skip more (a milestone gate).

Then two checks only you can make, from the registry:

- **Its areas are free.** Profile `areas: labels`: none of its area labels
  (profile `area prefix`) is shared with active work. Profile `areas: files`:
  none of the existing files its plan changes is held — every open loop PR
  that is not parked holds every path it changes, and paths collide when
  equal or when one directory contains the other. Profile `serial dirs`
  (migrations, anything numbered in sequence) are held whole: two plans that
  each add a migration conflict even when the files differ. If a PR's paths
  cannot be read, it holds everything.
- **It is not `too recent`** from an earlier run's claim ([`START.md`](START.md#what-an-earlier-run-left-behind)).

Take eligible tickets in order until the WIP limit, the caps or the budget
reservation is full. Parallel work in separate areas is how the loop keeps
pace; conflicts are its main cost.

**Claim before dispatching** (`harness claim ID`, [claims](RULES.md#claims)),
and append the dispatch to the [registry](RULES.md#the-registry). **If the
claim returns a `worktree`, the implementer works there**, on the branch it
names, instead of in an isolated worktree of its own: the tracker's session is
bound to it.

## The planner

With profile `plan step: on` (and always under `areas: files`), a read-only
planner goes first. It starts from the base branch explicitly, and returns:

- the plan, in a few steps
- **the existing files it changes** (the ticket's area) and the files it
  creates (which hold nothing)
- a size verdict: S or M, or a proposed split
- a cross-check by its own isolated child subagent, looking for stale
  assumptions about the repo, colliding sequence numbers, and tests or CI that
  already cover it. It removes the child's worktree and branch, and names them
  if it could not.

Log the plan, any disagreement between planner and child, and your reasoning.
The implementer gets the plan.

## The implementer's brief

The implementer works alone, in its own worktree, and never sees the rest of
the run. **Give it generous context**: every judgement call goes better if it
knows what the ticket is for.

**Context:** the product and what is valuable here (the profile's prose); what
this ticket serves; that a reviewer who knows nothing of its reasoning will
read the PR; whether the PR may merge unattended (profile `merge policy`).

**Point it at:** the ticket; the plan, if there is one; `CLAUDE.md`; the
repo's design direction and the `ui-craft` skill, for anything a user sees.

**Ask it for:**

- **A branch named per profile `branch`**, cut from the latest
  `origin/<base>` (fetched explicitly — its worktree did not start there).
- **Its own toolchain environment inside its worktree**, created before any
  install, with the environment's absolute path written into every command
  (shell state does not persist between commands; `activate` lasts one).
  Never restart a shared service.
- **An implementation that meets the acceptance criteria, and nothing more.**
  It reports anything out of scope it notices, and names every file it changed
  outside the plan.
- **Tests that would fail if an acceptance criterion were not met.**
- **The full gate (profile `gate`), passing**, with its summary lines quoted
  verbatim, skips and errors included, and the tool versions it ran. If a step
  cannot run in its environment, it says so and does not call it passed.
- **For anything visible:** run the app on its own `PORT`, look at every screen
  it touched, and hold them to the `ui-craft` rubric.
- **Before building, a check that the ticket's premise still holds** on the
  base branch. A ticket filed against an old state may already be done.
- **One push, at the end**: `git push -u origin BRANCH` for the new branch.
  Then a **draft** PR per the repo's PR template that carries the loop marker
  for profile `pr scope` (the loop label, or profile `loop line` in the body),
  **the exact line `harness ticket-line ID` prints** (give it to the
  implementer), and concrete verification steps, and **says what changed
  without arguing that it is right** — an argumentative body anchors every
  reviewer.
- **When it cannot finish:** no PR, and a report giving the reason (a
  decision needed; bigger than M, with a proposed split; the gate cannot run
  here). It never touches the tracker itself.
- **A one-line report**: the PR number, or the reason it stopped.

**Rules to pass explicitly:** the list in [dispatching](RULES.md#dispatching),
plus its slot and `PORT`.

## When it returns

**Check GitHub, not the report.** The PR exists, is a draft, targets the base,
is in scope (`harness pr N` → `inScope`), and its body carries the ticket
line. Append `finished:`, run `harness synced ID --cwd WORKTREE`, and end the
ticket's claim with `harness release ID "PR #n"`. The next lap routes the PR;
its first round is always cold.

**No PR, and the implementer reported why:** `harness release ID "REASON"`,
then:

- **Split proposed:** the split becomes new cards ([`PLAN.md`](PLAN.md#filing-cards)),
  and the original is closed as not planned, linking them.
- **Decision needed:** profile `decision label`, and a decision in the status.
- **Gate could not run here:** a hold, with a `loop:` comment naming the
  missing capability. The human releases it once the environment is fixed.

For GitHub, the labels are what keep the ticket from being dispatched again;
a reason held only in memory would not survive a compaction. Trackers without
labels (Skrypt) list these under **Needs you** in the status instead, and the
loop keeps them in the Now block so it does not claim them again this run.
