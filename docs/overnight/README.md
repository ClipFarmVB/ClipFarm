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
| [`README.md`](./README.md) | this file — the index, the reading protocol, and these figures. Every lap reads it | 2.0k |
| [`START.md`](./START.md) | once, at the start of a run — mode, scope, what it may push to, capability checks, how work is chosen | 5.4k |
| [`RULES.md`](./RULES.md) | **every iteration** — hard rules, evidence, the push test, logging, priority order, the ceiling and the budget, measuring what you publish, repo traps | 9.4k |
| [`REVIEW.md`](./REVIEW.md) | a lap that reviews a PR — markers, routing, posting, reading state, terminal labels | 14.9k |
| [`BRIEFS.md`](./BRIEFS.md) | a lap that spawns a round — the cold and semi-cold briefs, and how findings are tiered | 5.2k |
| [`FIX.md`](./FIX.md) | a lap that fixes findings — the cycle, the settle bar, choosing an `unsettled` reason | 5.6k |
| [`TICKETS.md`](./TICKETS.md) | a lap that implements a ticket, and whenever a card needs filing | 2.9k |
| [`REPORTING.md`](./REPORTING.md) | the end of the run | 2.1k |
| [`RATIONALE.md`](./RATIONALE.md) | optional background — what a night costs, why the machinery is shaped this way | 2.7k |

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
  **Spawning a round → `BRIEFS.md`**, on top of whichever of those you are on —
  it is the one file reached from two phases, because step 2 spawns semi-cold
  rounds as readily as step 1 spawns cold ones.
  **Filing a card → `TICKETS.md`**, whichever lap you are on. That is the one
  entry here that is not a phase: `FIX.md` sends you to a card for a nit you
  chose not to fix, and `review-only` runs never otherwise open `TICKETS.md`,
  so without this line the instruction to file one points at a file the
  protocol has just told you this lap does not need.
- **Re-read `RULES.md` in full after any compaction**, and say in the log that
  you did. Compaction is exactly when a half-remembered rule reads like a real
  one.
- **When memory and the file disagree, the file wins** — including against your
  own log. Two runs have been bitten by acting on a remembered version of a rule
  that had since been amended.

A step-1 lap that only selects costs about 26k tokens of brief instead of 50k;
one that also spawns a round, about 31k. A step-2 lap is about 22k, a step-3 lap
about 14k. That is the whole point of the split.

**`BRIEFS.md` is what makes the step-2 number work.** Before it, a lap fixing
findings had to load the whole of `REVIEW.md` to reach the semi-cold brief —
**18k** tokens, the size that file was *before* the split. Splitting it out took
that lap from **~32k to ~19k**. Those two are measured across the split, not on
today's files, because the lap they compare no longer exists here. The table
below cannot reproduce them; the command that can is right here:

```
git ls-tree -r --long '596755d^' -- docs/overnight/README.md \
    docs/overnight/RULES.md docs/overnight/FIX.md docs/overnight/REVIEW.md
```

sums to 32.25k, and the same four with `BRIEFS.md` in place of `REVIEW.md` at
`596755d` to 19.09k. Paths are spelled out because a brace expansion is a bash
feature: under `dash`, which is `/bin/sh` on Debian and Ubuntu, it matches
nothing, prints nothing and exits 0 — a silent empty answer in the one command
on this page that exists to be re-run.

**Do not try to recompute the before-figure from the table above.** Two things
stop it, and only fixing one still gives a wrong answer: that lap needs
`REVIEW.md` with the briefs still in it, which is `596755d^`'s file and not
this revision's — 18.13k, because it still held them — *and* none of the four
was the size the table records today, `README.md`, `RULES.md` and `FIX.md` all
having been smaller in August. So no substitution into today's row values lands
on the right answer, in either direction.

Two comparisons follow, answering different questions, so each says what it is
measured on — which is the thing this page gets wrong when it gets anything
wrong:

- **On today's files.** A step-1 lap that only selects is ~5k cheaper than one
  that also spawns: exactly `BRIEFS.md`, which only the spawning lap reads.
- **Across the CF-365 split**, at `596755d^` and `596755d`. A *spawning*
  step-1 lap went 26.70k to 28.05k, having gained a file. A select-only lap
  reads the same three files on both sides and went 26.70k to 23.60k.

**Everything on this page is bytes/4**, those included — 129000 bytes is the
32.25k, and the `ls-tree` block prints the bytes to check it. What varies is
the *revision*, and only ever between two values: the table and the lap figures
are the files as they stand; anything marked across-the-split is `596755d^`
against `596755d`, because the lap it compares no longer exists here. A lap is
this file plus `RULES.md` plus the phase files it reads — **this file's own row
included**, which is why there is one, and *files* plural because two laps read
two: a step-1 lap that spawns adds `BRIEFS.md` to `REVIEW.md`, and a step-2 lap
adds it to `FIX.md`. Each row is rounded to the nearest tenth, and a lap is
summed from bytes rather than from those rounded rows, so adding the rows up
lands within a tenth or so rather than exactly.
**Re-measure them when you add a section** — they had drifted by a third before
CF-275 re-took them, and this is the table a run reads to plan what it can
afford.

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
