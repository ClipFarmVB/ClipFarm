# Plan: cards, triage, and decisions

Read on a lap that **files a card, triages, or meets something that needs a
decision**. A pack (profile `pack`) adds its own planning on top: a spec lap,
milestones, a demo.

Part of the build brief. See [`README.md`](./README.md).

---

## What is valuable

The profile's prose says what makes work valuable in this repo: who uses it,
what its primary path is, what a defect there costs. Every priority decision
comes back to that. Without it, the default order is: a red base branch; a
broken primary path; what the ticket's acceptance criteria name; everything
else.

| priority | meaning |
|---|---|
| **P0** | the base branch is red; the primary path is broken |
| **P1** | primary-path work that does not work yet; what the current goal names |
| **P2** | depth, realism, edge states, docs |
| **P3** | anything nobody will notice soon. Rarely worth filing. |

## Filing cards

Cards come from work already under way: a review finding outside the PR's
scope, a gate failure, a rehearsal. **Do not go hunting.**

- **Check it is not already filed.** List every ticket, open and closed,
  paginated, and filter on the finding's *noun*, not its wording; read the
  two or three nearest. An open card covers it: add your evidence there. A
  closed card covers it and the problem is back: file a new one and link the
  old one as a regression.
- **The card must let someone act without asking you anything**: what, why,
  acceptance criteria a reviewer can check, where it came from, and evidence
  read for this card.
- **A review that declined to fix something "because a card was filed" must
  name the card.**
- **The ready label means the loop may build it unattended.** Apply it only
  under profile `self-approve cards: yes`, and only to cards that stay inside
  the current scope and need no decision. Otherwise the card waits for a human
  to add it.
- **At most five per lap.** Overflow goes in the status.
- **Open the body with** `Filed by the harness loop.`

For `tracker: skrypt`, cards are not filed by the loop at all: they are
listed in the status for a human to add ([`brief/trackers/skrypt.md`](../trackers/skrypt.md)).

## Re-triage

The backlog is a hypothesis about what matters, and the work teaches things.
**A triage fires when:**

- four PRs have merged since the last triage
- a human comments on the status
- a finding shows the plan is wrong
- **a PR becomes parked**
- **the WIP limit has room but no ticket is eligible**, for example because
  everything left depends on a parked PR

**Never triage twice for the same state.** Record `last triage: UTC` in the
Now block and wait for something to change.

**Dispatch one triager** with: the profile's prose; the open and recently
closed tickets; the open loop PRs with their states; recent merges; every
human comment since the last triage. It returns proposed changes —
re-prioritise, split, close as not planned (with the reason), new cards — each
with its reason, and for each parked PR whether to re-plan its ticket, close
the PR, or ask the human; and the biggest risk right now. Apply them subject
to the rules below and the card cap. Closing as not planned is reversible.

## What needs a decision

These go to the human rather than being decided by the loop:

- a change to what the product does, or who it is for
- adding a service that costs money or needs a sign-up
- changing the stack, a dependency's major version, or a public interface
- anything high-risk under the profile's definition
- anything irreversible or public

**On a ticket:** profile `decision label`, remove the ready label, and post a
`loop:` comment with the options, what each costs, and a recommendation.
Notify the human ([`REPORTING.md`](REPORTING.md#decisions)). Carry on with
other work. **A human removing the decision label counts as approval**; pass
their comment to the implementer.

**On a PR**, use `unsettled: needs a decision`, never the decision label.

A purely technical choice is not a decision for the human. Take the option
that is cheapest to reverse, and record why.
