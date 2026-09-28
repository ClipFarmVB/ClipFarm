---
name: harness-build-loop
description: (Repo-pinned copy of the harness plugin's build-loop skill, for sessions without the plugin; prefer harness:build-loop where the plugin is installed.) Start, resume or stop the autonomous harness loop in this repository. It reviews PRs with fresh subagents, fixes what they find, settles them, and in build mode turns ready tickets into reviewed PRs, merging only if the repo's profile allows it. Accepts a mode (build, review-only, plan-only) and an interval. Use when the user asks to start, resume, stop or run the loop, the build loop, an overnight run, review-only mode, or to work the backlog or the PR queue unattended.
argument-hint: "[build|review-only|plan-only|stop] [interval]"
---

# Build loop

Start a `/loop` that follows the build brief at
`${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/brief/loop/`. This skill starts and stops the loop. It
does not govern it. **Do not restate the brief's rules here or in the
prompt.**

The CLI is `node ${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/bin/harness.mjs` (call it `harness`
below). Run it from the repository's main checkout.

## What the user typed

| the user typed | mode | interval |
|---|---|---|
| `/harness-build-loop` | [you choose](#choosing-the-mode) | `10m` |
| `/harness-build-loop build` · `review-only` · `plan-only` | as given | `10m` |
| `/harness-build-loop build 5m` | as given | as given |
| `/harness-build-loop stop` | [stop](#stopping) | — |

Arguments: `$ARGUMENTS`

## Before starting, check and tell the user

Only report what is actually true right now.

1. **The profile.** `harness profile` must validate. If there is no
   `.claude/harness/profile.md`, offer `/harness-init` and stop.
2. **The checkout.** On the profile's base branch, clean apart from ignored
   files, with the base on the remote (`git ls-remote --heads origin BASE`).
   The orchestrator fast-forwards it every lap, and a feature branch here
   becomes every subagent's starting point.
3. **The log is gitignored**: `git check-ignore -q <profile log>`. If not, say
   so and stop; the log must never be committed.
4. **No other run.** `CronList` shows no harness loop in this session, and the
   log's Now block shows no run that has not ended. One live run at a time.
5. **Labels** (profile `labels: yes`): if `harness get repos/OWNER/REPO/labels --all` lacks the profile's
   labels, offer `harness labels`. It writes to GitHub: in a repo someone else
   owns, ask first.
6. **Permissions.** An unattended run stalls on its first prompt, and
   background subagents may not inherit the session's permission mode. The
   session needs a mode that will not prompt for `git`, `gh`, `node`, the
   profile's gate and toolchain, subagents and the scheduler. The run tests
   this on its first lap.
7. **What the loop touches.** Only PRs in profile `pr scope`. The human's
   veto is a hold (the hold label, or a comment starting `hold`). The status
   (an issue, or `.claude/harness/status.md`) is the dashboard, and replies there
   are read.
8. **The machine.** This session open and the machine awake for the run.

## Choosing the mode

- **Profile `pack: hackathon`** and no `hackathon/DECISION.md`: nothing to
  build. Point the user to `/harness-ideate` and do not start. Before
  `hacking starts`: `plan-only`.
- **Many open loop PRs, none settled** (`harness prs`): `review-only`.
- **No eligible ticket** (`harness tickets`, tracker `github`; the work-session
  plan, tracker `skrypt`) **but open loop PRs**: `review-only`.
- **Otherwise:** `build`.

**Say which mode, and why, in one line.**

## Starting

Invoke the `loop` skill with the interval and this prompt, with the mode
filled in:

```
Read ${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/brief/loop/README.md and follow the brief it indexes, per the reading protocol in it. The harness plugin root is ${CLAUDE_PROJECT_DIR}/.claude/harness/plugin. Read the Now block and latest entries of the run log first each iteration so you do not repeat work. Mode: MODE.
```

**Pass `Mode:` every time**: the brief records where its mode came from. Mode
is the only setting passed this way; everything else is the profile, which the
loop never edits. If the user asks to change `merge policy` or another
setting, say it is a profile edit for them to make.

**Use a fixed interval, never self-pacing.** A fixed interval re-arms itself
and fires a missed tick once. `10m` is the default; subagent completions also
wake the session. Recurring jobs expire after seven days.

**The plugin path is fixed for the run.** Updating the plugin mid-run
(`claude plugin marketplace update`) can move it; stop the run first.

## Stopping

When the user asks to stop:

1. Stop every background subagent the loop has in flight (the Now block's
   registry lists them).
2. Follow the end-of-run steps in
   `${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/brief/loop/REPORTING.md`.
3. Delete the recurring job, list the jobs again, and confirm it is gone.
