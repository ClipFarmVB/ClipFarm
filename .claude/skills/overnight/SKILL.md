---
name: overnight
description: Start an unattended overnight run against this repo's backlog, following the brief in docs/overnight/. Use when the user asks to run the overnight loop, start a night run, or work the queue unattended. Accepts a mode (review-only or build) and an interval.
---

# Overnight run

Start a `/loop` that follows the unattended-run brief in `docs/overnight/`.

## What to do

Invoke the `loop` skill with the interval and this prompt, verbatim:

```
Read docs/overnight/README.md and follow the brief it indexes, per the reading protocol in it. Re-read .claude/overnight-log.md first each iteration so you do not repeat work. Mode: <MODE>.
```

Substitute `<MODE>` and pick the interval from the arguments:

| the user typed | mode | interval |
|---|---|---|
| `/overnight` | `review-only` | `10m` |
| `/overnight build` | `build` | `10m` |
| `/overnight review-only` | `review-only` | `10m` |
| `/overnight 5m`, `/overnight build 20m` | as given | as given |

**Default to `review-only`, and default to a fixed interval — never self-pacing.**
A self-paced loop has to re-arm itself with `ScheduleWakeup` at the end of every
turn, and a run that forgets once stops silently, hours before dawn, with the log
un-truncated and no report issue. A fixed interval re-arms by construction. If
the user explicitly asks for self-pacing, say this once and then do as they ask.

**Say which mode you are starting, and why, before you start it.** If the user
gave no mode, the choice is yours and they should see it: check whether step 1
can clear (below) and name the reason in one line.

## Choosing the mode when the user did not

Step 3 — ticket work — runs **only when steps 1 and 2 are clear**. So `build` is
only meaningfully different from `review-only` when the PR queue is shallow
enough to clear inside the round budget. Check before choosing:

```
curl -s "https://api.github.com/repos/ClipFarmVB/ClipFarm/pulls?state=open&per_page=100" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d), 'open;', sum(1 for p in d if any(l['name']=='review-settled' for l in p['labels'])), 'settled')"
```

Many unsettled PRs against the 40-round budget means `build` will behave exactly
like `review-only` and then file a report with an empty "PRs opened" section,
which reads as a failed night rather than a reviewing one. Prefer `review-only`
and say so.

## Before starting, tell the user what this night cannot do

Only what is actually true tonight — check, do not recite:

- **The board.** Projects v2 is GraphQL-only and a cloud session refuses GraphQL
  outright, so `Status` will not move and the report will say the board was
  unverified. Cards still reach it: a project workflow adds new issues
  automatically.
- **`gh`.** Not installed in a cloud session (`command -v gh`). Every command in
  the brief is then a specification, not a command — `REVIEW.md` → *What a
  non-`gh` tool must provide* is the list that has to be satisfied by whatever
  replaces it.
- **Docker.** Absent in a cloud session, so any ticket whose verification needs
  `docker compose` or the eval harness gets a plan in the log and no PR.

## What not to do here

**Do not restate the brief's rules in this file.** `RULES.md` holds them, and a
second copy is the discardable rule-carrier the brief refuses everywhere — see
`README.md` → *The rules are not also summarised into a shorter file*. This skill
starts the loop; it does not govern it.
