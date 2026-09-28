# Reporting

Read whenever the **status** is due for a rewrite, whenever a **human is
needed**, and at the **end of the run**.

Part of the build brief. See [`README.md`](./README.md).

---

## The status

The human's dashboard. Profile `status: issue`: one issue titled `Harness
status — REPO`, labelled `status` if labels are allowed, pinned, created on the
first lap. Profile `status: file`: `.claude/harness/status.md` in the main
checkout (gitignored), for repos where the loop must not open issues.

**Its body is rewritten, not appended to.** Rewrite it on every merge, every
settle, whenever a new item needs the human, and at least once an hour.

**Order it by urgency:**

1. **Needs you.** Decisions, with links. Human actions (keys, sign-ups).
   Latched PRs, each naming what refused the push. PRs ready for a human merge,
   with their ship verdicts and whether they touch harness or high-risk paths.
   Commands that prompted for permission. Cards the loop may not file itself.
2. **The run.** Settings and where the mode came from; the run start; the
   round budget used; the WIP limit against what is in flight.
3. **In flight.** Loop PRs with their routing row, round count and checks;
   running implementers.
4. **Recently merged**, with what to try in each.
5. **Held back.** PRs waiting on checks; parked PRs with their reasons.
6. **Capability gaps**, and any gate step that could not run.
7. **Overflow cards**, one line each.

**Before rewriting, read the current body and any new comments.** A comment
without a machine prefix is the human talking: input to the lap, and a triage
trigger. The loop's own comments there start with `loop:`. Confirm each
rewrite by reading it back.

## Decisions

When something needs the human:

1. Put it under **Needs you**.
2. On the ticket or PR itself, post a `loop:` comment with the options, what
   each costs, and a recommendation.
3. **Send a push notification**, if this environment can: one line saying
   what is needed, with a link.

Then carry on with work that does not depend on the answer.

**Notify only for:** decisions and blocking human actions; a PR taking
`unsettled`; three consecutive usage-limit laps; a missing recurring job; the
run stopping. A notification on every merge gets muted, and then the one that
matters is missed too.

## End of run

When the run stops, for whatever reason:

1. **Clean up.** Every in-flight subagent is already stopped and logged
   `run ended`, its claims released. Pin any latched fix first. Then remove
   only the worktrees the registry names (`git worktree remove --force PATH`,
   `git branch -D` its agent branch). Name, but do not remove, agent worktrees
   newer than the run start that the registry does not know.
2. **Post the run report**: with `status: issue`, a `loop:` comment on the
   status issue; with `status: file`, a dated section in the status file.
   Title and first line carry the mode. It covers:
   - the settings; the login, token type, and rate limits at start and end
     (never the credential); whether GitHub reads were paginated throughout
   - **the stop reason**, in the words of [the stopping rule](RULES.md#stopping)
   - PRs merged; PRs settled and waiting for a human; PRs unsettled, by reason
     — latched ones with the refusal verbatim; PRs that hit the ceiling or the
     budget, listed apart from their reason
   - PRs never reached, apart from those out of scope; PRs held by a check,
     with its name and conclusion, and whether the base branch is red
   - cards filed; tickets abandoned and why; tickets released because an area
     was held, with the holder; files changed outside plans
   - every claim released unfinished: orphaned, too recent, lost, human action,
     run ended
   - the WIP limit against the most ever in flight, and the observed round and
     fixer durations, from the registry
   - decisions outstanding
   - **lessons**: each thing this run learned that should change the harness,
     naming the brief file and rule, or the CLI command, it would change
   - footer text you did not write, quoted; any agent that pinned a model
   - every error that stopped work, **quoted verbatim**

   Write a structurally empty section as "none — MODE run", not by omitting
   it. **Be honest.** A report that overstates what landed is worse than a
   short one.
3. **Confirm it exists** by reading it back.
4. **Only then, truncate the log.** It is gitignored, so a truncation after a
   failed post would lose the run.
5. Notify the human that the run has stopped, and why.
