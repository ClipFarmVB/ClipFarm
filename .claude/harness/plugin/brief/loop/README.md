# Build loop

Instructions for an agent running the harness `/loop` in a repository. The
loop:

- reviews PRs with fresh subagents, fixes what they find, and settles them
- in `build` mode, turns ready tickets into reviewed PRs
- merges what settles, if the repo's profile allows it
- reports to the human, and asks them only for decisions

Start it with `/harness-build-loop`. The skill fills in the prompt:

```
/loop 10m Read PLUGIN/brief/loop/README.md and follow the brief it indexes, per the reading protocol in it. The harness plugin root is PLUGIN. Read the Now block and latest entries of the run log first each iteration so you do not repeat work. Mode: MODE.
```

**PLUGIN** is the plugin's absolute path, written into the prompt at start.
Everywhere in this brief:

- `brief/…` means `PLUGIN/brief/…`
- **`harness …` means `node PLUGIN/bin/harness.mjs …`**, run from the repo's
  main checkout. It prints JSON. Its output is evidence; your memory of it is
  not.

**Use a fixed interval, never self-pacing.** A fixed interval re-arms itself,
while a self-paced loop stops for good the first time a lap forgets to
schedule the next one.

## The profile

Everything that differs between repositories lives in the repo's profile,
`.claude/harness/profile.md`: a ```` ```harness ```` block of settings, and
prose around it giving the product, what makes work valuable here, the gate,
and the repo's traps. `harness profile` validates and prints it. **If it does
not validate, stop and say why.** This brief names profile keys in the form
**profile `key`**.

The prose part is context for subagents. Pass the relevant parts of it to each
one, since subagents do not read this brief.

## The shape of it

The session running the loop is an **orchestrator**. It reads state, decides,
dispatches subagents and records what happened. **It never writes product code
and is never the reviewer.** The work is done by subagents:

| subagent | does | brief in |
|---|---|---|
| planner | (profile `plan step: on`) plans one ticket and names its files | [`TICKETS.md`](TICKETS.md#the-planner) |
| implementer | takes one ticket and opens a draft loop PR | [`TICKETS.md`](TICKETS.md#the-implementers-brief) |
| cold reviewer | reviews a PR it knows nothing about | [`BRIEFS.md`](BRIEFS.md#the-cold-reviewers-brief) |
| semi-cold reviewer | gives a verdict on each open finding | [`BRIEFS.md`](BRIEFS.md#the-semi-cold-reviewers-brief) |
| ship reviewer | (profile `ship reviewers`) a SHIP / NO-SHIP verdict on a settled head | [`BRIEFS.md`](BRIEFS.md#ship-reviewers) |
| fixer | acts on findings, human comments, red checks and conflicts | [`FIX.md`](FIX.md#the-fixers-brief) |
| triager | re-ranks the backlog | [`PLAN.md`](PLAN.md#re-triage) |

**The state lives on GitHub** (and, for profile `tracker: skrypt`, in the
work-session service): tickets, loop PRs, marker comments, `fix:` replies,
round reviews with finding IDs, and review labels or record comments. The run
log's Now block holds the rest. A lap that starts cold after a compaction
rebuilds everything from those.

## The files, and when to read each

Every rule lives in exactly one file.

| file | when to read it | ~tokens |
|---|---|---|
| [`README.md`](README.md) | every lap: this index | 1.4k |
| [`START.md`](START.md) | once, at the start of a run: settings, capability checks, orphans | 2.0k |
| [`RULES.md`](RULES.md) | **every lap**: hard rules, the lap order, dispatching, claims, limits, evidence, logging, traps | 4.5k |
| [`TICKETS.md`](TICKETS.md) | a lap that dispatches a planner or an implementer | 1.5k |
| [`REVIEW.md`](REVIEW.md) | a lap that routes, labels, handles human input, checks or merges | 2.5k |
| [`BRIEFS.md`](BRIEFS.md) | a lap that spawns a review round | 2.0k |
| [`FIX.md`](FIX.md) | a lap that dispatches a fixer, settles, or parks a PR | 1.8k |
| [`PLAN.md`](PLAN.md) | a lap that files a card, triages, or needs a decision | 1.0k |
| [`REPORTING.md`](REPORTING.md) | status updates, decisions, the end of the run | 1.1k |
| `brief/trackers/<tracker>.md` | first lap, and any lap that claims or releases a ticket | ~1k |
| `brief/packs/<pack>/README.md` | every lap, when profile `pack` is not `none` | ~3k |

Token figures are bytes ÷ 4. `npm test` in the harness repo checks them, so a
changed file changes this table in the same PR.

## Reading protocol

- **First lap:** `START.md`, `RULES.md`, the tracker file, and the pack file
  if there is one, in full.
- **Every lap after:** `RULES.md`, plus the files the lap uses. A lap that
  spawns a round reads `BRIEFS.md` on top of `REVIEW.md`. An idle lap where
  nothing has changed reads nothing more.
- **After any compaction:** re-read `RULES.md` in full, and note that you did.
  Acting on a remembered rule instead of the file is how runs drift.
- **When memory and a file disagree, the file wins.** When the log and GitHub
  disagree, GitHub wins. When this brief and the CLI disagree, stop and say so
  in the status: one of them is wrong, and guessing which is how a PR gets
  merged on a bug.

## Where things live

| path | what |
|---|---|
| `.claude/harness/profile.md` | the repo's settings and context |
| profile `log` (default `.claude/harness/log.md`) | the Now block and lap entries, for one run (gitignored) |
| `.claude/harness/status.md` | the status, when profile `status: file` |
| GitHub | tickets (tracker `github`), loop PRs, the status issue |

## Changing the brief

This brief lives in the harness plugin, not in the repo, so a run can never
edit the rules it is following. A running loop records what it learned as a
lesson in the run report, naming the file and rule it would change. The human
carries it to the harness repo's `LESSONS.md` and brief through a PR there.
