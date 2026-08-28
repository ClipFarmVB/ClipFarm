# Reporting

Read **at the end of the run**, before posting the report issue.

Part of the unattended-run brief — see [`README.md`](./README.md).

| file | when to read it |
|---|---|
| [`START.md`](./START.md) | once, at the start of a run |
| [`RULES.md`](./RULES.md) | **every iteration** |
| [`REVIEW.md`](./REVIEW.md) | a lap that reviews a PR |
| [`FIX.md`](./FIX.md) | a lap that fixes findings |
| [`TICKETS.md`](./TICKETS.md) | a lap that implements a ticket |
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
- **Whether the board was verified**, and if not, say so rather than saying
  cards are missing from it
- PRs reviewed, how many rounds each took and of which kind, and findings by
  tier
- PRs labelled `unsettled`, split by the four reasons their `unsettled:` comment
  gives — `needs a decision` (a reviewer found a judgement call), `latched` (a
  collaborator pushed to the branch, **or** the guard could not verify it),
  `not our branch` (the author's next push
  re-opens it), and `ran out of rounds` (the per-PR ceiling, or the run-wide
  budget) — and what is still outstanding on each
- **Latched PRs by name, each saying why it latched.** Two of these reasons want
  a human and want *different* humans doing different things: a judgement call
  needs the reviewer's question answered, a latch needs someone to decide what to
  do about a branch. A single "N need a human" figure hides that, which is the
  same signal loss the bullet below describes for the bounds.

  A latch has two causes and they want different responses, so name the cause,
  not just the state:

  > `#312` is latched — `@sam` has pushed to the branch, so the run may not.
  > 2 Medium and 3 nits are written up in the review. Someone needs to take this
  > one over: merge it, push the fix, or hand it back to `@sam`.

  > `#288` is latched — the guard could not verify it, because the branch has
  > more than 250 commits. Nobody may have pushed to it at all. Someone needs to
  > confirm whose branch it is; the findings are in the review either way.

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
