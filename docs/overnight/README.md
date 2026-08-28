# Unattended runs

Instructions for an agent running `/loop` with nobody watching — overnight, or
any stretch where questions cannot be answered.

Start a run with:

```
/loop Read docs/overnight/README.md and follow the brief it indexes, per the reading protocol below. Re-read .claude/overnight-log.md first each iteration so you do not repeat work.
```

## The files, and when to read each

The brief is split by **phase**, so a lap reads the rules it is about to use and
not the ones it is not. Every rule lives in exactly one file.

| file | when to read it | ~tokens |
|---|---|---|
| [`START.md`](./START.md) | once, at the start of a run — mode, scope, what it may push to, capability checks, how work is chosen | 4.8k |
| [`RULES.md`](./RULES.md) | **every iteration** — hard rules, the push test, logging, priority order, the ceiling and the budget, repo traps | 5.6k |
| [`REVIEW.md`](./REVIEW.md) | a lap that reviews a PR — markers, routing, posting, reading state, terminal labels, the reviewer briefs | 15k |
| [`FIX.md`](./FIX.md) | a lap that fixes findings — the cycle, the settle bar, choosing an `unsettled` reason | 4.2k |
| [`TICKETS.md`](./TICKETS.md) | a lap that implements a ticket, and whenever a card needs filing | 1.3k |
| [`REPORTING.md`](./REPORTING.md) | the end of the run | 1.2k |
| [`RATIONALE.md`](./RATIONALE.md) | optional background — what a night costs, why the machinery is shaped this way | 1.8k |

`.claude/overnight-log.md` is scratch memory for one run, gitignored on purpose.
**Read it at the start of every iteration**, and **truncate it as the last act
of the run**, once the report issue is posted and confirmed — see
[`REPORTING.md`](./REPORTING.md). The report is the copy that survives; the log
is not, so a lesson worth keeping has to reach `RULES.md` or a phase file in the
run that learned it.

## Reading protocol

- **First iteration: `START.md` and `RULES.md`, in full.** Not skimmed. The
  reasoning is what stops you applying a rule where it does not fit.
- **Every iteration after: `RULES.md`, plus the one phase file the lap needs.**
  Reviewing a PR → `REVIEW.md`. Fixing findings → `FIX.md`. Implementing a
  ticket → `TICKETS.md`. Writing the report → `REPORTING.md`.
- **Re-read `RULES.md` in full after any compaction**, and say in the log that
  you did. Compaction is exactly when a half-remembered rule reads like a real
  one.
- **When memory and the file disagree, the file wins** — including against your
  own log. Two runs have been bitten by acting on a remembered version of a rule
  that had since been amended.

A step-1 lap costs about 21k tokens of brief instead of 34k; a step-2 lap about
11k; a step-3 lap about 8k. That is the whole point of the split.

**The rules are not also summarised into a shorter file.** Splitting by phase
keeps exactly one copy of each rule, with the reasoning that produced it still
attached; a summary would be a second copy, and the copy nobody amends is the
one someone reads. The rule this follows from — *nothing that is discardable may
carry a rule* — is stated in [`RULES.md`](./RULES.md) rather than restated
here.

## Changing the brief

Amend a rule **where it lives**, not in this index. A fix written into a section
the next reader is told to replace has not been made.

Change it the way it has been changed so far: one concern per PR, with the card
that motivated it. `CF-270` (#311) moved the push rule from the run to the
account; `CF-271` (#317) did the routing tables; `CF-265` (#297) let a converged
PR settle. A single sweeping rewrite of a file this dense is how a rule gets
dropped silently.

If a run learns something that should change how future runs behave, that
belongs in `RULES.md` or the phase file it governs — even when the lesson came
from tonight's scope. `START.md` holds what is true of *this* run only, and is
rewritten each time, so nothing durable may live there.
