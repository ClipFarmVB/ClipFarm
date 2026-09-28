# Run start

Read **once, at the start of a run**: the run's settings, the capability
checks, and what an earlier run left behind. None of it changes mid-run.

Part of the build brief. See [`README.md`](./README.md).

---

## Settings

The run's settings are the profile (`harness profile`) plus one thing from the
starting prompt: **`Mode:`**. Mode is the only setting that may arrive that
way. Everything that decides what the loop may do unattended — `merge policy`,
`pr scope`, `round cap`, `wip limit` — takes a human writing it in the
profile, and the loop never edits the profile.

**If `Mode:` is missing or unrecognised, stop and ask.**

**Write the settings you are operating under, and where the mode came from,
into the Now block now**, and into the status once it exists.

### Modes

| mode | tickets | review, fix | merge |
|---|---|---|---|
| `build` | implement eligible tickets | yes | per merge policy |
| `review-only` | filing cards only | yes | per merge policy |
| `plan-only` | triage and file cards; no implementers | no | no |

**How each mode ends:**

- **`build`** runs until nothing is eligible, nothing is in flight, and no loop
  PR needs a round, a fix or a merge — or until a pack's clock says it is over.
- **`review-only`** ends when no loop PR needs a round, a fix or a merge.
- **`plan-only`** ends once the triage it was started for is filed.

### Merge policy

- **`auto`**: the run squash-merges loop PRs that pass [the merge
  test](REVIEW.md#merging). Right for a solo repo where ticket N+1 builds on
  ticket N and a queue of unmerged drafts stacks up conflicts.
- **`human`**: the run settles PRs and lists them in the status. A human
  merges.

Harness paths and profile `high risk` paths always wait for a human,
whatever the policy ([`RULES.md`](RULES.md#loop-prs-and-loop-comments)).

### WIP limit

The most work in progress at once (profile `wip limit`). It counts:

- implementers (and planners) that have not yet opened a PR
- open loop PRs that are **not** parked

A PR is **parked** while it is `unsettled`, on `hold`, or settled and waiting
for a human merge. Parked PRs do not count, or a few PRs waiting on the human
would stop all work.

- **Settled PRs waiting for a merge still hold their areas.** They are about to
  land, and new work in the same area would conflict with them.
- **Other parked PRs release their areas.**

**Size it to the backlog, not to caution.** Every PR takes several review
rounds, so a limit of 3 against a forty-ticket backlog is serial in practice:
one run started there and spent its opening hours waiting on rounds, then ran
at 6 without trouble. It is safe as high as three conditions hold: **the gate
tests its own worktree** (below), **areas are fine-grained enough not to
collide**, and **the machine can run that many gates at once**.

**Per-run caps.** With profile `pr cap` above 0, before each implementer add
the PRs this run opened to the implementers in flight; at the cap, dispatch
none. With profile `round budget` above 0, see [the
budget](RULES.md#the-round-ceiling-and-the-budget).

## First: establish what you can actually do

Check each item and log the results in one block. **Name every gap in the
status.**

- **Identity and repository.**
  - `harness whoami` answers with a login, the transport (`gh`, or `rest`
    through curl in a cloud session) and the repo. **If it has no login,
    stop**: `own` and `loop-body` scopes cannot be computed without it, and
    must never widen.
  - `harness rate` logs the rate-limit headers and the token kind (never the
    credential). Read the headers, not `rate_limit`, which has reported
    `used: 0` while the headers moved. In a cloud session the proxy's token
    posts as you (via the Claude app), so authorship rules still hold.
  - `git ls-remote --heads origin <base>` shows the base branch. If not, stop.
  - The local checkout is on the base branch and clean, apart from ignored
    files. **A feature branch here becomes every subagent's starting point.**
- **GraphQL.** Everything goes through `harness` over REST. The one GraphQL-
  only step is marking a draft ready before a merge; in a cloud session that
  needs the GitHub MCP tool ([merging](REVIEW.md#merging)). No `gh` is not a
  gap; no `curl` is.
- **Labels** (profile `labels: yes`). `harness labels` creates them. It writes
  to GitHub, so run it only in a repo you own or where the profile says the
  team agreed.
- **CI.** Whether the base branch carries a workflow under `.github/workflows/`
  (`harness base` reports `hasWorkflow`).
- **The gate.** The profile's `gate` command exists. Note every service whose
  absence makes tests *skip* rather than fail (a database, a browser); a green
  gate with skips is short by that many tests.
  - **It must test its own worktree's code.** An install shared across
    worktrees resolves to whichever tree installed last, and the gate goes
    green on another branch's source. The main checkout *contains* every
    worktree under `.claude/worktrees/`, so a path-prefix check is not proof.
    If the profile says nothing about how the gate proves this, name it as a
    gap and run with the WIP limit at 1.
  - **Its smoke step must fail on a product that runs but does nothing** (an
    empty screen, an empty result). One run's smoke step passed for a day and
    a half on a product that rendered nothing, because it checked only an exit
    code. The settle bar leans on this step.
- **Subagents.** Dispatch one throwaway background subagent with worktree
  isolation. It reports, verbatim: `git rev-parse --show-toplevel`,
  `git rev-parse --path-format=absolute --git-common-dir`,
  `git remote get-url origin`, `node PLUGIN/bin/harness.mjs whoami`, and
  `git push --dry-run origin HEAD:refs/heads/harness-probe` (contacts the
  remote, creates nothing), plus whether anything prompted for permission.
  - **Its common-dir or origin differs from this session's:** stop. The
    session started in the wrong repo, and every worktree it hands out is of
    that repo. A differing path alone does not prove this either way.
  - **A permission prompt:** an unattended run stalls on the first one. Stop
    and say which command prompted.
  - **No worktree:** run with the WIP limit at 1, one subagent at a time.
  - Log `git config core.hooksPath`: an absolute path makes every worktree
    commit run this checkout's hooks.
- **Notifications.** Whether this environment can send a push notification.
- **Secrets, by name only.** Which keys the profile says the gate or app
  needs, whether a local env file exists (`test -f`), and the Actions secret
  names if `harness get repos/OWNER/REPO/actions/secrets` answers (it may not
  in a cloud session). **Never read, print or copy a value here.**

## One live run at a time

Markers and counters carry no run identity. A second concurrent run's
markers count as this run's, its pushes void this run's rounds, and it halves
the credential's rate limit. **If the log's Now block shows a run that has
not ended, or `CronList` shows another harness loop, stop and ask.**

## Start the log

Write `run start: UTC` as the first line of the log — the **result** of
`harness now`, never a literal `$(date …)`: `$` sorts below digits, so every
"since the run start" count would silently become all-time. Then write the
Now block ([`RULES.md`](RULES.md#logging)).

## What an earlier run left behind

Stopping a loop removes its schedule, not the subagents it spawned, and a
crashed run leaves claims behind.

- `harness claims` lists every target carrying the claim label. For each one
  it calls **`orphaned`** (its newest own `claimed:` comment is older than both
  this run's start and 120 minutes): release it —
  remove the claim label, then comment `released: UTC — orphaned by an earlier
  run`.
- **`too recent`**: leave it, and do not select it this run. Something may
  still be working on it.
- **`not ours`**: another account's claim. Leave it.
- Worktrees under `.claude/worktrees/` that no open PR or ticket needs, and
  that have no uncommitted changes: list them in the status for the human to
  remove. Never remove a worktree with changes, or a `-latched-` pin branch.
