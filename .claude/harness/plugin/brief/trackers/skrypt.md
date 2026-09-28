# Tracker: Skrypt work sessions

Read on the first lap, and on any lap that claims or finishes a ticket, when
profile `tracker: skrypt`. It applies to repos the work-session CLI supports
(skrypt-os, automations). The repo's `CLAUDE.md` and the `skrypt-work` skill
are the authority on everything here; this file says how the loop uses them,
and where they disagree, they win.

Part of the build brief. See [`brief/loop/README.md`](../loop/README.md).

---

## What changes from the GitHub tracker

- **Tickets are `SK-####` tasks** on the Skrypt OS board, not issues. **The
  loop's queue is the plan the CLI ranks for this account**: `pnpm work:session
  plan` (or bare `sage`). "Do these first" is the engine's order — never
  reorder it, add to it, or work a task that is not in it. **Assigning a task
  to this account is the approval**; the loop picks nothing the engine did not
  hand it.
- **The harness commands drive the repo's own CLI** (profile `tracker
  command`, default `pnpm work:session`), and never its API or database:

  | harness | runs | where |
  |---|---|---|
  | `harness tickets` | `plan --json` (text until the flag exists) | the main checkout |
  | `harness claim SK-N` | `bootstrap --task SK-N` | the main checkout |
  | `harness synced SK-N` | `sync --task SK-N` | the session's worktree |
  | `harness release SK-N OUTCOME` | `end --task SK-N`; nothing if OUTCOME is `PR #n` | the session's worktree |

  An error from the CLI (an expired token is a 401) stops the command with
  its message. It is never read as an empty plan.
- **Claiming is starting a session.** `bootstrap` creates the worktree
  `.claude/worktrees/sk-n` on branch `<you>/sk-n`, flips the task to in
  progress, and writes `WORK.md` and `CONTEXT.md`. Re-running it is safe.
  **This replaces worktree isolation for the implementer**: dispatch it with
  that worktree's absolute path, and it runs `pnpm install` there first. It
  replaces the claim label and comment too; the registry line still applies.
- **Never touch the board directly.** No SQL on `tasks`, `work_sessions` or
  token tables, no status edits outside the CLI, and **never close a task**:
  the person who accepts the work closes it. Cards the loop would file go in
  the status under **Needs you** for a human to add.
- **The PR body carries `Ticket: SK-N`** (profile `ticket line`) and profile
  `loop line`, and the PR is a draft.
- **Skrypt Context must be green at every head.** Pushes from the session's own
  worktree advance it automatically, and `harness synced SK-N` after each push
  re-checks it once. If it is still red, name it in the status; do not work
  around it.
- **No labels.** The repo has only GitHub's defaults, and creating labels is a
  shared-repo change. The profile uses `labels: no` and `pr scope: loop-body`:
  state lives in record comments, and a human's `hold` comment is the hold.
- **Status is a file** (profile `status: file`): the loop opens no issues in a
  team repo.
- **Abandoning** a ticket: `harness release SK-N REASON` ends the session;
  then list the worktree in the status for removal. Merging the PR ends the
  session by itself, so a ticket whose PR opened is released by doing nothing.
- **What `harness tickets` returns here**: `eligible` is the engine's "Do
  these first", minus tasks you already hold a session on; those are
  `resumable` (resume in their worktree); one of your in-progress tasks with
  no work session is `skipped` — you are working it by hand, so the loop
  leaves it alone.

## Merging is never the loop's

Profile validation refuses `merge policy: auto` for this tracker. The
unattended harness runs **drafts first, even where a ruling would allow a
builder session to merge its own PR** (`docs/epics/autonomous-harness.md`),
and a settled head waits for a person. The loop's job ends at a PR that is
ready for that person:

- settled by cold rounds, with the checks green (`test`, lint, build, and
  **Skrypt Context**)
- **Sonnet SHIP and Sol SHIP at that exact head** (profile `ship reviewers:
  sonnet, sol`); a head that changed after a verdict needs new ones
- not high-risk (below)

List it under **Needs you** with its head SHA, both verdicts, and whether it
is high-risk. The person merging decides; the loop never records anything a
later reader could take as merge approval.

## High-risk is always a person's word

The repo's `CLAUDE.md` defines it: schema migrations (they may merge; applying
to prod is a separate ceremony), RLS / grants / auth / tokens / secrets, the
Sage review lifecycle and the work-session / Context machinery, anything that
deletes or rewrites data, and prod environment flips. Profile `high risk` lists
the paths; a reviewer who finds a high-risk change elsewhere raises it as a
Critical tagged `high-risk`, which routes to `needs a decision`. Name the
specific reason in the status, for the orchestrator or the owner to take.

## Fleet etiquette

- **Report to the SPOC, not to everyone.** Anything that needs the founder
  goes in the status for the owner of this run to carry; the loop never
  messages people itself.
- **Never edit a task row that anchors an open PR** once its Context packet is
  pinned: the packet hash covers it.
- **One owner per CI watch.** The loop reads check results when it routes; it
  does not poll a run another session owns.
- **Sol for bulk work.** Where a subagent's job is bounded drafting or
  analysis, `codex exec -m gpt-5.6-sol` is preferred over a Claude subagent;
  orchestration, gate decisions and board work stay with Claude.
