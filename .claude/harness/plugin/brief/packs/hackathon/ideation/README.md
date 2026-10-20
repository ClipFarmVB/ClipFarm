# Ideation loop

Instructions for an agent running the ideation `/loop`, which turns a
hackathon's track descriptions into a short, researched, adversarially tested
set of project ideas, then stops and hands the choice to a human.

Start it with `/harness-ideate`, or by hand:

```
/loop 5m Read PLUGIN/brief/packs/hackathon/ideation/README.md and follow the brief it indexes, per the reading protocol in it. The harness plugin root is PLUGIN. Re-read .claude/ideation-log.md first each iteration so you do not repeat work.
```

**PLUGIN** is the harness plugin's absolute path, written into the prompt by
the skill. Paths in this brief like `RULES.md` are relative to this file;
`hackathon/…` and `.claude/…` are in the repository.

**Why this loop exists.** When implementation is cheap, the idea is what
decides a hackathon. A good idea here is not one that sounds clever; it is one
a tired judge remembers at the end of the day, that fits the track and the
sponsors' prizes, that has not been done forty times, and that the build loop
can make work reliably, with a polished UI, before the deadline. Every phase
below exists to test ideas against one of those conditions.

**The loop ends with a decision packet, not a decision.** Choosing the idea is
the human's call. The loop's job is to make that choice easy and well-informed.
Then it stops.

## The files, and when to read each

Every rule lives in exactly one file. A lap reads the rules it is about to use.

| file | when to read it |
|---|---|
| [`README.md`](./README.md) | every lap: the index, the reading protocol, the state layout |
| [`RULES.md`](./RULES.md) | **every lap**: hard rules, independence, evidence, logging, the phase machine, stopping |
| [`INTAKE.md`](./INTAKE.md) | the intake lap: turning the tracks into a brief, research, the saturation list |
| [`GENERATE.md`](./GENERATE.md) | a lap that generates or evolves ideas: lenses, the idea schema, the generator brief |
| [`JUDGE.md`](./JUDGE.md) | a lap that pools, scores or red-teams ideas: rubric, panel, aggregation, the red team |
| [`SHORTLIST.md`](./SHORTLIST.md) | the last laps: one-pagers, spikes, the decision packet, the handoff to a human |

## Reading protocol

- **First lap: `RULES.md` in full, then the phase file its phase machine
  names.**
- **Every lap after that: `RULES.md`, then the one phase file the lap needs.**
- **After any compaction, re-read `RULES.md` in full** and note in the log that
  you did.
- **When memory and a file disagree, the file wins.** That includes your own
  log. When the log and the artifacts on disk disagree, the artifacts win.

## Where things live

Event inputs are written by the human and rewritten for each event. Nothing in
them may carry a rule.

| path | what | who writes it |
|---|---|---|
| `hackathon/EVENT.md` | dates, judging criteria, prizes, rules, constraints | human |
| `hackathon/tracks/` | track and sponsor descriptions, in any format | human |
| `hackathon/ideation/` | everything this loop produces (layout in [`RULES.md`](RULES.md#files-ledgers-and-completeness)) | this loop |
| `hackathon/ideation/PACKET.md` | the latest decision packet, the human's starting point | this loop |
| `hackathon/ideation/FEEDBACK.md` | the human's `refine`, `decide` and `note` entries, **binding on later rounds** | `/harness-ideate`, on the human's behalf |
| `hackathon/DECISION.md` | the chosen idea, which the build loop reads | `/harness-ideate decide` |
| `.claude/ideation-log.md` | scratch memory for one run | this loop |

`hackathon/ideation/`, `hackathon/DECISION.md` and the log are gitignored,
because this repository is public and a committed shortlist publishes the ideas
before the event. After submission, the human may choose to publish the
ideation record, since the write-up draws on it.

## Changing the brief

Amend a rule where it lives, one concern at a time, through a PR. **A running
loop never edits this brief.** It writes lessons to
`hackathon/ideation/LESSONS.md`, and the packet lists them for the human to
apply.
