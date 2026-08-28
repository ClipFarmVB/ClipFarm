# Unattended runs

Everything governing an unattended `/loop` run lives in this folder. One file
it depends on does not: the run's scratch log, which is gitignored because it
belongs to a single run.

| File | What it is | When to read it |
|---|---|---|
| [`REFERENCE.md`](./REFERENCE.md) | The brief. Every rule, with the reasoning and the incident that produced it. | In full, on the first iteration of a run. |
| `.claude/overnight-log.md` | Scratch memory for one run. Gitignored on purpose. | At the start of **every** iteration. |

Start a run with:

```
/loop Read docs/overnight/README.md and follow docs/overnight/REFERENCE.md exactly, per the reading protocol in the README. Re-read .claude/overnight-log.md first each iteration so you do not repeat work.
```

**Update the "This run" section of `REFERENCE.md` before starting.** An agent
given a stale scope works confidently on the wrong things.

## Reading protocol

`REFERENCE.md` is long, and the naive instruction — *read it in full every
iteration* — costs roughly 30k tokens per lap before any work happens. Over a
night that is a meaningful fraction of the budget spent re-reading rules that
did not change.

It is long for a reason. Nearly every rule is stated with the incident that
produced it, because this repository has repeatedly found that a rule without
its reason gets "simplified" by the next reader and the bug comes back. That is
worth its length on the first read and worth nothing on the ninth.

So:

- **First iteration of a run: read it in full.** Not skimmed. The reasoning is
  what stops you from applying a rule where it does not fit.
- **Every iteration, before you finish: §Log before you finish each iteration.**
  It is the one step that runs on every lap regardless of what the lap did, and
  the log is the only thing that survives compaction.
- **Before pushing anything: §Hard rules and §The push test.** Pushing is the
  highest-risk action in the loop and the one a half-remembered rule gets wrong
  in the expensive direction — under the pre-2026-08-25 rule a whole night's
  queue was parked unfixable on a misread of exactly this. Re-read both; they
  are short.
- **Otherwise re-read only what the work in front of you touches.**
  About to route on a marker → §Routing. About to settle → §The cycle and the
  settle bar. About to label → §The terminal labels and their reasons. About to
  post → §Posting a round. About to file a card → §Filing cards.
- **Re-read in full after any compaction**, and say in the log that you did.
  Compaction is exactly when a half-remembered rule reads like a real one.
- **When memory and the file disagree, the file wins** — including against your
  own log. Two runs have been bitten by acting on a remembered version of a rule
  that had since been amended.

The rules are not duplicated into a shorter file on purpose. A summary would be
a second copy of every rule, and this brief's own doctrine — *nothing that is
discardable may carry a rule* — cuts both ways: a rule that lives in two places
drifts in one of them, and the uncorrected twin is the single most common defect
found in this repository's review rounds.

## Changing the brief

Amend it **where the rule lives**, not in a preamble or a summary. A fix written
into a section the next reader is told to replace has not been made.

Change it the way it has been changed so far: one concern per PR, with the card
that motivated it. `CF-270` (#311) moved the push rule from the run to the
account; `CF-271` (#317) did the routing tables; `CF-265` (#297) let a converged
PR settle. A single sweeping rewrite of a file this dense is how a rule gets
dropped silently. (No tally of those PRs here on purpose: this brief's own index
says a sentence promising a number goes stale the moment another lands.)

If a run learns something that should change how future runs behave, that
belongs under Standing policy — even when the lesson came from tonight's scope.
