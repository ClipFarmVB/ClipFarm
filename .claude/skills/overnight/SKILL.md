---
name: overnight
description: Start an unattended overnight run against this repo's backlog, following the brief in docs/overnight/. Use when the user asks to run the overnight loop, start a night run, or work the queue unattended. Accepts a mode (review-only or build) and an interval.
---

# Overnight run

Start a `/loop` that follows the unattended-run brief in `docs/overnight/`.

## What to do

Invoke the `loop` skill with the interval and this prompt, with `<MODE>`
substituted:

```
Read docs/overnight/README.md and follow the brief it indexes, per the reading protocol in it. Re-read .claude/overnight-log.md first each iteration so you do not repeat work. Mode: <MODE>.
```

| the user typed | mode | interval |
|---|---|---|
| `/overnight` | `review-only` | `10m` |
| `/overnight build` | `build` | `10m` |
| `/overnight review-only` | `review-only` | `10m` |
| `/overnight 5m`, `/overnight build 20m` | as given | as given |

**Say which mode you are starting and why, before you start it.** If the user
gave no mode, the choice is yours and they should see it in one line.

## The mode passes in the prompt — it is not edited into the repo

`Mode: <MODE>` in the starting instruction **overrides** the `mode:` value in
`START.md` → *This run*, per the paragraph there that grants it. Nothing is
edited, committed or merged to change the mode of a night.

**Pass it on every run, including when it matches the file.** The brief requires
the run to say which source the mode came from, and a prompt that omits it
silently falls back to whatever the block last said — which is the coupling this
is here to remove.

**Mode is the only value that may be passed this way.** `review scope` stays in
the file on purpose: `all` means reviewing other people's PRs unattended, which
the brief says takes a human setting it for that run. If the user asks for a
scope change, tell them it is a `START.md` edit, and why.

## Choosing the mode when the user did not

Step 3 — ticket work — runs **only when steps 1 and 2 are clear**. So `build`
differs from `review-only` only when the PR queue is shallow enough to clear
inside the round budget. Check, do not assume:

```
curl -s "https://api.github.com/repos/ClipFarmVB/ClipFarm/pulls?state=open&per_page=100" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d), 'open;', sum(1 for p in d if any(l['name']=='review-settled' for l in p['labels'])), 'settled')"
```

Many unsettled PRs against the 40-round budget means `build` behaves exactly like
`review-only` and then files a report with an empty "PRs opened" section, which
reads as a failed night rather than a reviewing one. Prefer `review-only` and say
so.

## Interval

**Use a fixed interval. Never self-pacing.** A self-paced loop re-arms itself at
the end of every turn, and a run that forgets once stops silently, hours before
dawn, with the log un-truncated and no report issue. A fixed interval re-arms
automatically. If the user asks for self-pacing anyway, say this once and comply.

What the fixed form actually does, since it governs what interval to pick:

- It is a **wall-clock cadence** — a cron schedule, not a gap measured from the
  end of the last iteration.
- **It never interrupts.** A due tick fires between turns; if the run is
  mid-iteration it waits for that turn to end.
- **There is no catch-up.** A tick that comes due during a long iteration fires
  **once** when the run goes idle, not once per interval missed. So a short
  interval against long iterations degrades to "start the next lap promptly",
  which is harmless — it cannot stack up laps or skip the queue.
- Units are `s`, `m`, `h`, `d`. Seconds round up to a minute; an interval that is
  not a clean cron step (`7m`, `90m`) is rounded and the choice is reported back.
  Minimum one minute.

`10m` is the default here because a lap that re-reads the brief and runs a round
takes longer than that anyway, so the interval is rarely the thing gating
progress. `5m` is not harmful, by the no-catch-up rule.

**Recurring tasks expire seven days after creation** — the task fires one last
time, then deletes itself. Irrelevant for a single night; worth knowing before
anyone treats this as a standing job.

## Before starting, tell the user what this night cannot do

Only what is actually true tonight — check, do not recite:

- **The board.** Projects v2 is GraphQL-only and a cloud session refuses GraphQL
  outright, so `Status` will not move and the report will say the board was
  unverified. Cards still reach it: a project workflow adds new issues
  automatically.
- **`gh`.** Not installed in a cloud session (`command -v gh`). Every command in
  the brief is then a specification, not a command — `REVIEW.md` → *What a
  non-`gh` tool must provide* is the list whatever replaces it has to satisfy.
- **Docker.** Absent in a cloud session, so any ticket whose verification needs
  `docker compose` or the eval harness gets a plan in the log and no PR.

## What not to do here

**Do not restate the brief's rules in this file.** `RULES.md` holds them, and a
second copy is the discardable rule-carrier the brief refuses everywhere — see
`README.md` → *The rules are not also summarised into a shorter file*. This skill
starts the loop; it does not govern it.
