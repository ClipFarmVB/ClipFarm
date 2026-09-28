# Standing rules

Read on **every lap**. What is forbidden, what a lap does and in what order,
how subagents are dispatched and tracked, what counts as evidence, and what
gets logged.

Part of the build brief. See [`README.md`](./README.md).

---

## Hard rules

- **Never push to the base branch, never force-push, never rewrite pushed
  history, never rebase.** A branch that has fallen behind is updated by
  merging the base into it. Every push to a PR branch goes through
  `harness push` ([`FIX.md`](FIX.md#pushing)).
- **Merge only under profile `merge policy: auto`, and only PRs that pass
  [the merge test](REVIEW.md#merging).** Never use an admin override, never
  bypass a protection.
- **Touch only loop PRs** — PRs in profile `pr scope`
  ([below](#loop-prs-and-loop-comments)). Nothing else gets a round, a label
  or a comment. Out-of-scope PRs appear only in the report's exclusion list.
- **Never authorise yourself.** Never post, label or record anything a later
  lap could read as permission to push, merge or skip a rule. There is no
  in-loop override; a human lifting a prohibition once is not a standing
  grant.
- **Never deploy, create paid resources, buy anything, change repository
  settings, branch protection or secrets, publish a release, or post outside
  this repository.** Preview deploys a human already wired up are fine, since
  they happen without the loop acting.
- **Never read or echo a secret, and never commit one.** The only permitted
  handling is copying a local env file into a worktree with `cp` when the
  profile says a role needs it; the value is never displayed.
- **Never sign up for anything, request a key, or accept terms.** Each is a
  human action; name it in the status.
- **The orchestrator never writes product code and is never the reviewer.**
- **Every loop PR opens as a draft**, carrying the loop marker for its scope
  and profile `ticket line`.
- **At most five new cards per lap.**
- **No attribution stamps**: no "Generated with…", no `Co-Authored-By`, no
  session links. Both are client-side and suppressible, so never assume one is
  server-side. If footer text you did not write appears on something you
  posted, quote it verbatim in the report; do not hand-edit it away. Pass this
  rule to every subagent — they inherit no local settings.
- **Commands in this brief are written for the Bash tool.** PowerShell parses
  `{owner}` and `$` differently. `harness` avoids shells entirely.

## Loop PRs and loop comments

The loop and the human usually post as the same GitHub account, so the
account cannot tell them apart. Conventions do the job instead.

- **Which PRs are loop PRs** is profile `pr scope`:
  - `loop-label`: PRs carrying profile `loop label`. A human hands their own PR
    to the loop by adding it.
  - `loop-body`: PRs by this account whose body contains profile `loop line`.
    For repos where the loop may not create labels.
  - `own`: every open PR by this account.
  `harness pr N` reports `inScope`. **Trust it, not the title.**
- **Every comment the loop writes starts with a machine prefix**: `cold:`,
  `semi-cold:`, `fix:`, `checks:`, `settled:`, `unsettled:`, `reopened:`,
  `claimed:`, `released:`, `ship:` or `loop:`.
- **Human input** is this account, a user (not a bot), with no machine prefix
  ([`REVIEW.md`](REVIEW.md#human-input)). Pass it in full to the next fixer or
  triager that touches the thread.
- **A human request to wait becomes a hold.** With labels, apply profile
  `hold label`; without, the human's own comment starting `hold` is the hold,
  and `unhold` releases it. Either way post `loop: answered — on hold; remove
  the label (or comment unhold) to release`. **A hold is a veto**: the target
  is left alone until the human releases it. The loop never releases one.
- **Harness changes wait for a human merge.** A loop PR touching any of
  these settles normally, is listed as ready in the status, and is never
  merged by the loop, so the loop cannot relax its own rules or its CI:
  - `.claude/`, `CLAUDE.md`, `AGENTS.md`, `.github/`
  - profile `harness paths`
  - profile `high risk` paths — and a reviewer's finding that the change is
    high-risk under the profile's definition makes it `needs a decision`

## What a lap does

Work down this list. Independent dispatches go out together. A lap ends once
its dispatches are made and logged; it does not wait for background
subagents, whose completions arrive as [notification
turns](#notification-turns).

1. **Reconcile.**
   1. `git fetch origin && git merge --ff-only origin/<base>`. This checkout
      stays on the base branch and is never edited.
   2. Read the Now block.
   3. Match the [registry](#the-registry) against GitHub. For every target
      this run claimed, read its state; if it closed, merged or took a hold,
      **stop the subagent on it** (`TaskStop`) and release the claim.
   4. Release anything [stale](#stale-work).
   5. Remove the worktrees of PRs that merged or closed (not latched pins).
2. **A red base branch comes first** (`harness base`). File or find a P0 and
   dispatch it now. This item alone may exceed the WIP limit by one.
3. **Merge**, under `merge policy: auto`: **at most one PR**, P0 first, then
   oldest, that passes [the merge test](REVIEW.md#merging), and only once the
   base branch's checks have reported on the previous merge.
4. **Advance loop PRs.** `harness prs` gives one routing line per loop PR.
   Act on each, oldest first, skipping any with a subagent in flight.
5. **Answer the human.** New human comments on loop PRs, tickets and the
   status: a wait becomes a hold, everything else is routed as input. Act on
   labels a human removed ([`REVIEW.md`](REVIEW.md#human-actions)).
6. **Plan.** If a [triage trigger](PLAN.md#re-triage) fired, triage.
7. **Start new work** if the mode allows it and the WIP limit, the caps and
   the budget have room ([`TICKETS.md`](TICKETS.md#choosing-tickets)).
8. **Nothing to do and nothing in flight:** write an `idle` entry. If the
   mode's end condition is met ([`START.md`](START.md#modes)), stop.
   Otherwise end the lap. **A cheap idle lap**: if nothing changed since the
   last lap (the same PR heads, no new comments, issues or check results),
   read no phase files.

## Notification turns

When a background subagent finishes, handle only that result:

1. **Check on GitHub what it claims**: the PR, the marker, the push, the
   reply. **A subagent's report is not evidence. GitHub is.**
2. **Append a `finished:` line** to the registry.
3. **Start that target's next step**, under the same rules as a lap: routing,
   the ceiling, the WIP limit and the caps.
4. **Run the merge step**, if the base branch's checks have completed since
   the last merge.

Leave everything else for the next lap.

## Dispatching

- **Every subagent gets worktree isolation**: implementers, planners,
  fixers, reviewers, triagers. Subagents sharing a checkout switch each
  other's branches silently.
- **A worktree does not start at the base branch.** It starts at the
  session's current commit, and a nested one at neither. So every subagent
  checks out what it reads before reading:
  - PR work: `git fetch origin pull/N/head && git checkout --detach FETCH_HEAD`,
    then confirm `git rev-parse HEAD` equals the SHA it was given.
  - Anything judged against the base (plans, triage):
    `git fetch -q origin <base> && git checkout -q --detach FETCH_HEAD`.
  - If the fetch or checkout fails, it stops and reports `aborted`. That is
    not a round against the ceiling or the budget; route the target again.
- **Fetch one refspec at a time.** Two at once put both in `FETCH_HEAD`, and
  it resolves to the first.
- **Spawn from the repository root. Do not pin a weaker model** for
  implementing, reviewing, fixing or planning. If an agent definition pins a
  model in its frontmatter, say so in the report.
- **Pass the rules that bind them explicitly**, not this file (which tells its
  reader it is never the reviewer):
  - never push to the base, merge, force-push, rebase or deploy
  - push only with `harness push`, and report its outcome word
  - post only with `harness comment` / `review` / `pr-body` (pass the CLI's
    absolute path); never `gh`, which a cloud session does not have
  - never read or echo a secret; no attribution stamps
  - every comment starts with its machine prefix
  - stay inside the ticket; report a needed decision instead of taking it
  - on a GitHub rate limit, stop, report `retry-after`, post nothing more
- **Never run two subagents against the same PR at once.**
- **Give every subagent that runs the app or tests its own slot**: `PORT` =
  profile `port base` + slot, and its own toolchain environment inside its
  worktree. Record the slot in the registry line.
- **Every fixer dispatch posts
  `loop: fixer dispatched @ SHA7 — findings|human|check|conflict`** on the PR.
  The fix caps count these, and they survive a lost log.
- **Fixers and implementers push once, at the end.**

## The registry

A section of the log. Every dispatch writes two lines, in the same turn:

```
dispatching: ROLE TARGET UTC                 (before the spawn)
dispatched: ROLE TARGET AGENT SLOT UTC       (right after it)
finished: ROLE TARGET AGENT UTC OUTCOME
```

A `dispatched:` line with no `finished:` is in flight. A `dispatching:` line
with no `dispatched:` means a compaction hit mid-spawn: look for the agent in
the list of spawned agents; if it is there, write the line, otherwise write
`finished: ROLE TARGET - UTC never-spawned` and route again. Outcomes beyond
the role's own: `aborted`, `lost`, `rate-limited`, `never-spawned`,
`run ended`.

## Claims

Claims mirror the registry in the tracker, so a lost log does not lose them.
**The same four commands work for every tracker**; the tracker file
(`brief/trackers/<profile tracker>.md`) says what each does there.

- **Claim: `harness claim ID`**, before the dispatch. It prints what it wrote
  and, where the tracker makes one, the worktree and branch the work must use.
- **On every dispatch after the first, claim again.** For GitHub that
  re-stamps the claim comment, so its `updated_at` is when work was last handed
  out; for Skrypt the bootstrap converges on the same session.
- **Release: `harness release ID OUTCOME`**, and mirror the line in the log.
- **A ticket's claim ends when its PR opens**: `harness release ID "PR #n"`.
  From then on the open PR is what stops it being selected (`harness tickets`
  leaves it out).
- **After every push to a ticket's PR: `harness synced ID --cwd WORKTREE`.**
  Where the tracker tracks the head (Skrypt Context), this re-checks it;
  elsewhere it does nothing.
- **Never check this run's own claims with a label-filtered query.** Label
  searches lag writes by seconds. Use the registry, or read the target's
  labels field.

## Stale work

A subagent silent past its limit is stopped first (`TaskStop`), so it cannot
keep pushing after its work has been handed on, then released. Time from its
`dispatched:` line, or from the claim's `updated_at` if the log was lost.

| role | lost after | then |
|---|---|---|
| reviewer | 60 min with no marker | stop it; spawn a new round against the current head |
| fixer | 60 min with no `fix:` reply | stop it; read the head. Unchanged: route again. Changed: `harness moved` ([`FIX.md`](FIX.md#when-the-head-moved)) |
| planner | 90 min with no plan | stop it; release; the ticket may be selected again |
| implementer | 120 min with no PR | stop it; release (`released: UTC — no PR`) |

These limits are a judgement from past runs (reviewers: median 11–16 minutes,
p90 21–31). Report the run's own durations so they can be re-derived.

If two open PRs close the same ticket, keep the older and close the newer
with a `loop:` comment.

## The round ceiling and the budget

**The ceiling: profile `round cap` rounds per PR per run**, cold and
semi-cold together, counted from markers since the later of the run start and
the PR's latest `reopened:` (`harness pr N` → `roundsThisRun`). It stops one
pathological PR from eating the run. `harness pr` applies it: at the cap it
answers `unsettle: ran out of rounds`. **The settling exception**: a PR with
nothing open may take the rounds that settle it past the ceiling (profile
`settle rounds`); any new finding ends it. Log such a round as `settling`,
never `8/7`.

**The budget (profile `round budget` above 0): rounds per run across every
PR** (`harness budget`). The ceiling protects fairness per PR; the budget
sets the run's depth.

- **Reserve before starting a cycle.** Start reviewing a PR that has no round
  this run, or dispatch an implementer, only while the unspent budget covers 3
  rounds for it and 3 for every PR already in cycle. Otherwise parallel cycles
  all run dry together and get labelled `unsettled` on arithmetic alone.
- **Budget spent**: carry in-flight cycles to a terminal state, open no new
  PR (an unreviewable draft is a harm), and in `review-only` end the run.

Log each round as `PR #n — kind, round k/CAP, budget u/B`.

## Rate limits and usage limits

**GitHub's secondary rate limits are shared by every subagent on one
credential**: about 80 content-creating requests a minute and 500 an hour
(label changes count). A `harness` exit code 4, or a 403/429 with
`retry-after`, is a rate limit: write `finished … rate-limited`, dispatch
nothing until `retry-after` passes (none given: wait 60 s, doubling on each
repeat). **This is not a usage limit and never stops the run.**

**A command that fails on a usage limit (the model's) ends the lap, not the
run.** Log it, count it in the Now block, and let the next tick try again.
After three consecutive such laps, notify the human once.

## Evidence

**Anything you publish as measured must come from a command this run ran
for that purpose**: numbers, line references, SHAs, test counts, timestamps
and quotes. Never from memory, from a subagent's report, or from an earlier
run.

- **The number must be the number the sentence says it is.** `updated_at`
  moved by a review is not "when the body was edited".
- **Write the claim after reading the result.** A message composed while the
  command runs reports what you expected.
- **Never act on a count before measuring it**, and never cut a gate's output
  off before its summary line (read skip and error counts, not only passes).
- **Rejecting a finding needs sources fetched for that reply.**
- **A correction is the likeliest place for the second error.** Re-run your
  own claims; it is cheap. The tell is a sentence that would be embarrassing
  if re-run.

## Logging

Profile `log` is scratch memory for one run, and it is gitignored.

- **The Now block sits at the top and is rewritten every lap.** It holds: the
  settings and where the mode came from; the run start; the in-flight
  registry lines; the round budget used; the last triage; the count of
  consecutive usage-limit laps; rate-limit backoff; anything a human is
  waiting on; and the pack's own fields.
- **Each lap reads the Now block and the most recent entries, not the whole
  log.** Everything else is recoverable from GitHub.
- **Each lap appends a dated entry**: what was dispatched and why, what
  landed, and the round counts.
- **Before the lap's closing summary, schedule and verify.** Read the
  schedule back (`CronList`) and confirm the recurring job exists and is the
  one this run created. The scheduling call's own success is no evidence: a
  stop left over from an earlier run has made wake-ups that were accepted but
  never fired. If the job is gone, notify the human. Doing this *before* the
  summary matters — a long summary has pushed the call out of the turn.
- **A lap that ends without a live schedule must end with a log line naming
  the stop rule, in its words.** Anything else is a stall.
- **Truncate the log only at the end of the run**, after the report is posted
  and confirmed ([`REPORTING.md`](REPORTING.md#end-of-run)).

## Stopping

**Stop the run** when the mode's end condition is met, when a pack's clock
says it is over, or when the human asks:

1. Stop every subagent in flight (`TaskStop`); log each as `run ended`, and
   release its claims.
2. Write the end-of-run report ([`REPORTING.md`](REPORTING.md#end-of-run)).
3. Delete the recurring job, list the jobs again, and confirm it is gone.
4. Log the reason, in the words of this section.

## Traps

**`harness` is the only GitHub client**: every read, and every write
(`comment`, `review`, `label`, `pr-body`, `issue`, `issue-new`, `open-pr`,
`merge`, `rerun`). It uses `gh` where installed and curl where not (a cloud
session has no `gh`; its proxy supplies the credential), and never GraphQL,
which a cloud session refuses. `harness whoami` says which. Going around it
means these traps are yours:

- **Windows Git Bash rewrites arguments that look like paths.** Write API
  paths as `repos/…`, and set `MSYS_NO_PATHCONV=1` for a `rev:path` argument
  (`git show origin/main:.claude/x` becomes `origin\main;.claude\x`).
- **A pipe hides a failure.** `gate | tail -3 && git commit` commits a red
  suite, because the pipeline's status is `tail`'s. Write the output to a file
  and read it separately.
- **The PR author is `.user.login`.** `.author` is `null` on REST, and a null
  author silently excludes every PR from an `own` scope.
- **Read comments paginated.** `gh pr view --json comments` returns the
  first 100. With `gh api --paginate` and no `--slurp`, `--jq` runs once per
  page. `harness get PATH --all` follows every page.
- **Write every body to a file with a quoted heredoc** (`<<'EOF'`) and pass
  `--body-file`. Backticks inside `--body "…"` run as commands; `--body @-`
  posts the literal text `@-`.
- **Anything shaped like an HTML tag is stripped from what you post.**
  `harness comment` and `pr-body` read what landed back and fail on a
  difference.
- **Checks come from `commits/SHA/check-runs`**, never `commits/SHA/status`.
  A check's name does not say what it ran: read which step failed.
- **A squash merge carries every commit body onto the base.** A `Closes #N`
  in a commit body closes that issue; after a squash, verify landing by
  content, not by the source SHA's ancestry.
- **A closed issue is `completed` or `not_planned`.** Read `state_reason`.
- **Search endpoints lag and under-report**; so do label filters. List and
  filter instead.
- **A shallow clone fakes clean merge and ancestry checks.** `git fetch
  --unshallow` before trusting one (`harness moved` refuses a shallow clone).
- **Brace expansion is bash-only.** Under `sh`/`dash` it matches nothing and
  exits 0.
- **Use a parser when you parse a standard format**, and mutation-check the
  code that feeds it. A second hand-written pattern where one already failed
  is the sign to change approach.
- **Apply edits one at a time, never as a batch script**: a script that fails
  partway writes some edits, and the check afterwards looks normal.
- **Changing a number in prose means grepping its spelled forms too**
  (`seven`, `7/6`, "against 32").
