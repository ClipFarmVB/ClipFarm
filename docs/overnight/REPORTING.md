# Reporting

Read **at the end of the run**, before posting the report issue.

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

### Reporting

Post the run report as a GitHub issue titled `Overnight run — <YYYY-MM-DD>`,
labelled `chore`. It joins the project the same way cards do — automatically,
with Sprint unset — so there is nothing to add by hand and nothing to report as
missing. A log file in a sandbox is a report nobody reads; the issue is the
copy that arrives.

**Name the mode in the title when it is not `build`** —
`Overnight run (review-only) — <YYYY-MM-DD>`. The point is that the title should
say what the run was *for*: a reader scanning issues can otherwise not tell a
night of queue work from a night of ticket work without opening it.

It also separates two runs of different modes on one date. It does **not**
separate two runs of the *same* mode on one date — nothing here does, and if
that happens, disambiguate in the title however makes sense at the time and say
in the first line which run this was.

**State the mode and the review scope in the first line of the report**, whichever
mode ran. A reader cannot otherwise tell "reviewed nothing new" from "was not
looking".

**State the WIP limit beside them, and the most targets that were in flight at
once** (CF-563) — read off the registry, not remembered. The two differ when
the queue could not fill the limit or a capability check forced it to 1, and
the difference is what an operator needs before raising it. **List every
ticket released because its area was held, with the PR that held it**, and
every file an implementer changed outside its plan: those are the two
measurements of how often parallel work collides.

**Before writing the report, stop what is still in flight and release it**
(CF-562). Stop every subagent with a `dispatching:` or `dispatched:` line and no
`finished:`, write
its `finished:` line with the outcome `run ended`, and release its claim with
`released: <UTC> — run ended`. A run that stops cleanly should hand the next one
no claims at all; [releasing at run start](RULES.md#stale-claims) is the backstop
for a run that could not stop cleanly, not the plan.

**Name every claim this run released that it did not finish**, in its own list:
those released at run start as orphaned by an earlier run, those left alone
because they were too recent to release, those stopped as lost, those released
because a person merged, closed or held the target, and those released because
the run ended. Each is work somebody should
know did not complete. An orphaned claim in particular is the only evidence that
the previous run ended without reporting.

**Remove the worktrees this run's subagents left behind** (CF-564), once each
one's work is pushed or released. The harness's own documentation of worktree
isolation says it cleans a worktree up only if it is unchanged, and every
implementer and fixer changes its tree, so at six agents a night, each with its
own virtualenv, they pile up. On 2026-09-27 `git worktree list` held eleven
`agent-*` worktrees whose agents had long finished, the oldest from 2026-09-14,
and 68 of the `worktree-agent-*` branches the harness makes beside them. Each
worktree sits at `.claude/worktrees/agent-<AGENT>`, on a branch
`worktree-agent-<AGENT>`, `AGENT` being the id on its `dispatched:` line:

```
git worktree remove --force .claude/worktrees/agent-<AGENT>
git branch -D worktree-agent-<AGENT>
```

**Only those the registry names**, and **never a latched fix's**: its commit
exists nowhere else, and deleting the branch would lose it. Keep it and name it
in the report. Other worktrees under `.claude/worktrees/` belong to people and
sessions this run knows nothing about. Name in the report any it could not
remove.

### Then reset the log, and only then

`.claude/overnight-log.md` is scratch memory for **one** run. Once the report
issue exists, the log has no reader left: everything durable is in the issue,
and everything a later run needs about a PR is in that PR's markers and labels.
Left alone it accumulates — it reached 288K, some 74k tokens, across four runs,
re-read on every iteration of every night that followed, which cost more per lap
than the whole brief.

So, as the **last action of the run**, in this order:

1. Post the report issue.
2. **Confirm it exists** — read back the issue number or URL the API returned.
3. Only then truncate the log to empty.

**Never truncate before step 2.** A truncate that runs after a failed post loses
the night with nothing to show for it, and that is unrecoverable — the file is
gitignored, so there is no version of it anywhere.

Do not write a summary into the log on the way out. The issue is the copy that
survives; a second copy in a file that is about to be deleted is the discardable
rule-carrier this brief refuses everywhere else. The next run writes its own
`run start:` line as its first act, into an empty file.

**A lesson that mattered does not survive this.** If a run learned something
that should change how future runs behave, it belongs in
[`RULES.md`](RULES.md) or the phase file it governs, in the same run that
learned it. Truncation is what makes that non-optional: a lesson left only in
the log is gone at dawn.

The report contains:

- **Which tool you used for GitHub state** — `gh`, MCP, something else — and
  whether you confirmed it paginates. If you could not confirm it, say marker
  reads were unverified
- **Any footer text appended to your posts that you did not write**, quoted
  verbatim, once
- **Whether the board was verified**, and if not, *which* of the three reasons —
  GraphQL refused outright (a cloud session, unfixable there), GraphQL refused
  as **rate-limited** (its own budget, separate from the `core` numbers below,
  so this one is invisible in them), or the `project` scope missing (local,
  fixable) — rather than saying cards are missing from it. The three want
  different people: nobody, whoever schedules the run, and whoever grants the
  scope
- **The API identity and ceiling logged at run start**, and the remaining budget
  at the end. Identity means the login and whether `X-OAuth-Scopes` was empty —
  **never the credential itself**, which [must not be echoed
  anywhere](RULES.md#hard-rules) and least of all into a public issue. These are
  what turn "the run kept failing on GitHub" into a diagnosis: an empty scope
  list says it ran on an App installation token, and a remaining near zero says
  the night was spent on requests rather than on work.

  **Say which resource the number describes** — `X-RateLimit-Resource`, which
  for everything this brief does is `core`. A healthy `core` reading says
  nothing about GraphQL or about the secondary limits neither the headers nor
  `rate_limit` report, and a report that omits it invites exactly the wrong
  conclusion from a number that looks fine
- PRs reviewed, how many rounds each took and of which kind, and findings by
  tier
- **PRs held back from settling by a check**, each with the check's name and its
  conclusion — or `queued`/`in_progress`, or that the head had no check runs at
  all. These PRs carry **no label** (see [the settle
  bar](FIX.md#the-cycle-and-the-settle-bar)), so this bullet is the only place
  they are visible; without it they are indistinguishable from PRs the run never
  reached. CF-275 is the case: six rounds against red CI and nobody told, on a
  failure that was an upstream incompatibility rather than anything in the diff.

  **Also say whether the same check is red on `main`'s head**, which is cheap to
  read and separates "this PR broke it" from "the runner or an upstream pin broke
  it". Read `main`'s head, not the PR's `base.sha` — that is the base as of the
  PR's last sync and can be many commits behind. **This is informational and no
  rule acts on it:** both cases block settling identically, and deliberately, since
  a run cannot fix an upstream breakage unattended either way. It is here because
  the two want different humans, and a report that does not say which is which
  sends the question to the wrong one.
- PRs labelled `unsettled`, split by the five reasons their `unsettled:` comment
  gives — `needs a decision` (a reviewer found a judgement call), `latched` (the
  harness refused the push), `not our branch` (the author's next push re-opens
  it), `ran out of rounds` (the per-PR ceiling, or the run-wide budget), and
  `head moved` (someone pushed while this run was fixing it) — and
  what is still outstanding on each
- **Latched PRs by name, each saying exactly what refused the push.** Two of
  these reasons want a human and want *different* humans doing different things:
  a judgement call needs the reviewer's question answered, a latch needs someone
  to unblock the push. A single "N need a human" figure hides that, which is the
  same signal loss the bullet below describes for the bounds.

  Name the refusal, not just the state — the error, and what you had tried to
  push:

  > `#312` is latched — the push was refused by the harness (`permission denied
  > by the auto mode classifier`), retried once with the same result. 2 Medium
  > and 3 nits are written up in the review, and the patch is in the
  > `unsettled:` comment. Someone needs to push it, or grant the run the
  > permission.

  **A latch should now be rare and is worth treating as an environment fault.**
  Until 2026-08-29 the commonest cause was a collaborator's commit on the branch;
  that condition was removed from the push test after it fired on ten of eleven
  PRs in one queue and was a false positive every time. What is left is the push
  machinery genuinely saying no, which is closer to something broken than to a
  routine outcome — so if latches are common again, report *that* as the finding
  rather than the individual PRs.

  **These PRs cannot be returned to the loop by anything a run does**, which is
  why the report line is not optional: it is the only place a latched PR is
  visible, and the only prompt anyone gets. A run that latches a PR and does not
  name it has parked work where nobody will find it.

- **Which PRs hit the ceiling or the budget**, whatever reason they ended up
  labelled with. A PR that hit the ceiling *and* carries a judgement call is
  filed under `needs a decision`, so the reason breakdown above is no longer a
  reliable count of what the bounds bound — say it separately or the signal is
  lost
- Whether the review budget ran out, and which PRs never got a first round at
  all — with a deep queue this is the expected shape of a run, not a failure
- **Which PRs the `review scope` filter excluded**, and the scope the run used.
  Keep this **separate from the bullet above**: "never got a first round" means
  the run ran out before reaching it, and "out of scope" means it was never going
  to. Collapsing the two is exactly the confusion
  [Scope](START.md#scope-whose-prs-get-reviewed) forbids, and that distinction is the
  whole safety argument for defaulting the filter on
- PRs opened, with card and branch — and **what to test to verify each one**
- Cards filed, one sentence each on why
- Tickets attempted but abandoned, and why
- Every decision needing a human call
- Anything that failed, **verbatim** — do not summarise errors away

**In `review-only` mode two of those bullets are structurally empty** — PRs
opened (with the what-to-test notes that belong to it) and tickets abandoned.
Say "none — review-only run" rather than dropping the headings: an absent section
reads as an oversight, and the next run's reader cannot tell which it was. Cards
filed is **not** one of the empty ones.

Be honest. A report that overstates what landed is worse than a short one.
