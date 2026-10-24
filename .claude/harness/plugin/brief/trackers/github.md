# Tracker: GitHub issues

Read on the first lap, and on any lap that claims, releases or files a ticket,
when profile `tracker: github`.

Part of the build brief. See [`brief/loop/README.md`](../loop/README.md).

---

**Tickets are GitHub issues.** `harness tickets` lists the eligible ones
([`TICKETS.md`](../loop/TICKETS.md#choosing-tickets)); `harness linked` lists
the issues an open PR already closes; `harness claims` lists claimed targets.

## A ticket

Titled `type: what`. The body:

```
**Size:** S

## What
## Why
What it serves.
## Acceptance
- [ ] statements a reviewer can check against the running app or the tests
## Notes
Likely files, and dependencies as "Depends on #n".
```

Labels: a type (`feat`, `bug`, `ui`, `chore`, `docs`), a priority (`P0`–`P3`),
area labels (profile `area prefix`) under `areas: labels`, and profile
`ready label` where it applies. **Size S or M only**; an L is split before it
is filed. A PR a reviewer cannot hold in their head gets a worse review.

If the repo has its own ticket template (`.github/ISSUE_TEMPLATE/`), use that
instead, keeping the Acceptance section.

## Claiming

`harness claim #N` comments `claimed: UTC` (or re-stamps this account's
existing claim comment), then adds profile `claim label`. `harness release #N
OUTCOME` removes the label, then comments `released: UTC — OUTCOME`. The order
matters: a cut-off run leaves a harmless comment, never a label nothing can
release. `harness synced` does nothing here. A ticket's claim ends when its PR
opens (`harness release #N "PR #n"`).

## Closing

A merged PR closes its ticket through profile `ticket line` (`Closes #N`) in
its **body**. Confirm the ticket closed as `completed` after each merge. Never
put a closing keyword in a commit body for a different issue: a squash merge
carries commit bodies onto the base, and closes it.

## Numbering

If the repo numbers tickets in the title (`CF-123`), re-derive the next number
before every card from one newest-first page of issues, never from memory.
Parallel filers have produced duplicate numbers.
