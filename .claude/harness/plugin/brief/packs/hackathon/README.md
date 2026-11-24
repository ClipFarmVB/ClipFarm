# Pack: hackathon

Read on **every lap** when profile `pack: hackathon`. It adds a clock, a spec
lap, milestones and a submission to the build brief, and tightens what may
start and merge as the deadline nears. Where this pack and the build brief
disagree, this pack wins for the event.

Part of the build brief. See [`brief/loop/README.md`](../../loop/README.md).

---

## What wins

The judged artifact is **a three-minute demo, plus a write-up and a repo a
judge might open**. Value is whatever moves those, weighted by the event's
published judging criteria. A feature a judge never sees is worth close to
nothing; a rough edge on the demo path costs more than a missing feature off
it. **The demo path is the primary path** for the `ui-craft` skill and the
review tiers.

| priority | meaning |
|---|---|
| **P0** | the base is red; the demo path is broken; a submission requirement is still missing in polish or submit |
| **P1** | a demo beat that does not work yet; the wow beat; a targeted prize requirement |
| **P2** | depth a judge sees if they poke around; realistic data; the README |
| **P3** | anything a judge will not see in three minutes |

**Order:** priority, then milestone, then demo-beat order, then unblocked
before blocked, then smaller.

Recommended profile settings: `merge policy: auto`, `self-approve cards: yes`,
`wip limit: 3`, `areas: labels`.

## Where things live

| path | what |
|---|---|
| `hackathon/EVENT.md` | the clock, judging criteria and prizes (human-owned) |
| `hackathon/DECISION.md` | the chosen idea (from `/harness-ideate`). Gitignored until the spec PR adds it. |
| `hackathon/draft/` | the planner's drafts before the start (gitignored) |
| `hackathon/SPEC.md`, `DESIGN.md` | the build spec and the design direction |
| `hackathon/rehearsals/` | rehearsal reports and screenshots (gitignored) |
| `hackathon/submission/` | the submission materials |

Add `hackathon/EVENT.md` and, once merged, `hackathon/SPEC.md` to profile
`harness paths`: the loop must not relax its own event or spec.

## The clock

At run start, read `hacking starts`, `feature freeze`, `submission deadline`
and `submit window` from `EVENT.md` and convert each with
`harness utc 2026-10-03 18:00 America/Toronto`. **Never with `date`**: Git
Bash on Windows has no timezone data and treats every zone as UTC without an
error. Write each converted time into the Now block with its local form and
zone, so a wrong conversion is visible.

- **`feature freeze: default`:** the deadline minus 20% of the hacking window,
  and never less than 3 hours before the deadline.
- **A time is `TODO`, or the command fails:** stop and ask.

Every lap takes "now" from `harness now` and computes the phase:

| phase | from → to | what the loop may start |
|---|---|---|
| **pre-start** | before `hacking starts` | `plan-only`, local only (below) |
| **build** | start → freeze | everything |
| **polish** | freeze → deadline − `submit window` | P0 bugs, demo-path fixes, `ui` polish, `submission` tickets. No new features. |
| **submit** | the last `submit window` | no new implementers; only P0 and `submission` PRs advance and merge |
| **over** | from deadline − 10 minutes | nothing: stop every subagent, report, and stop |

- **A deadline backstop.** At run start, schedule a one-time job for deadline
  − 10 minutes: `Stop the build loop now, per brief/loop/RULES.md "Stopping".`
- **A lap that sees the phase change** rewrites the status first. Entering
  polish triggers a triage and the draft checklist ([`SUBMIT.md`](SUBMIT.md)).
- **In submit**, dispatch nothing not expected to finish before deadline − 10
  minutes, and **read the clock again immediately before every merge**. Many
  events disqualify commits after the deadline.

**Before `hacking starts`, only `plan-only` runs, and nothing that could
reveal the idea reaches GitHub.** The planner writes its drafts into
`hackathon/draft/`: `SPEC.md`, `DESIGN.md`, `backlog-draft.md`. It opens no PR,
pushes nothing, files nothing. Many events forbid code written before the
start. A `build` or `review-only` run started early says why it cannot start
yet, and stops.

## The spec lap

When there is no spec on the base and no spec PR open, dispatch one planner.
**Give it the absolute paths of `hackathon/DECISION.md` and the drafts** —
both are gitignored, so they do not exist in its worktree. It also reads
`EVENT.md` and the `ui-craft` skill. The human's notes at the top of
`DECISION.md` override everything. After the start, it copies `DECISION.md`
into its worktree (`git add -f`), starts from the drafts, and opens one draft
loop PR with `DECISION.md`, `SPEC.md`, `DESIGN.md` and the backlog draft
(local IDs `D1`, `D2` for tickets and dependencies). **Open questions do not
hold the spec PR**: the spec lists them, and only the tickets that depend on an
answer wait.

**The spec PR's cold round reviews by hand**, for faithfulness to
`DECISION.md`, every demo beat reachable through the milestones, every targeted
prize requirement ticketed, a source and a fake for each external dependency, a
concrete gate, S/M tickets with checkable acceptance, fine-grained areas, and
`DESIGN.md` meeting `ui-craft`. Anything that would build the wrong product is
Critical.

### What `SPEC.md` contains

1. **The product.** One-liner, user and story.
2. **The demo script.** Numbered beats with timestamps, the wow beat marked,
   and for each beat its screens, behaviour and fallback. Every P1 ticket maps
   to a beat.
3. **Prize requirements**, each quoted with its source and how it is met visibly.
4. **Architecture**: components, data model, where the hard part lives, every
   external service behind an adapter.
5. **Stack and conventions**, and the **areas**: one `area:` label per screen or
   module. Coarse areas make every ticket collide.
6. **The gate**: install, lint, typecheck, test, build and smoke-test behind
   **one package script** (profile `gate`). CI runs it on **every** PR with no
   path filter (a PR with no check runs can never settle) and on every push to
   the base, cancelling a superseded run on a PR but **never on a push to the
   base** — two close merges would otherwise leave a commit whose gate never
   finished. The gate runs in fake mode; takes its dev-server port from `PORT`
   with no server reuse; **resolves its own package and fails if it lies
   outside the worktree's source** (not merely under its path — build output
   and installed copies sit inside the tree too); **smoke-tests an outcome**
   on the demo path, not an exit code; and excludes `.claude/**` from every
   glob, since agent worktrees live there.
7. **Environment**: variable names only, and how fake mode switches on.
8. **Milestones**, with exit criteria (below).
9. **The cut list**, in order. 10. **Risks and spikes.** 11. **Open questions**,
   each naming the tickets it blocks.

**Keyless development.** Every external service sits behind an adapter with
a real implementation and a fake backed by realistic fixtures. Fake mode is
what CI runs, what implementers build against before keys exist, and the
demo's fallback — judges see its data, so it has to look real. Live mode runs
only where `.env.local` has keys: the rehearser, and an M2 implementer
verifying the hard part, making only the few calls the check needs.

**Stack defaults** when `DECISION.md` names none: TypeScript, Next.js (App
Router), Tailwind and shadcn/ui; Vitest and Playwright; one deployable unit;
SQLite only if persistence is needed.

### Milestones

| milestone | exit criterion |
|---|---|
| **M0 Skeleton** | scaffold, CI running the gate, design tokens, the app shell, fake adapters, a smoke test opening the first demo screen. The first M0 ticket carries CI and deletes the backlog draft. |
| **M1 Demo path** | every beat works end to end, on fakes |
| **M2 Wow** | the hard part works for real where keys exist, with a fallback elsewhere |
| **M3 Prizes** | every targeted prize requirement visibly met |
| **M4 Polish** | every demo screen passes the `ui-craft` rubric at 1440×900 |
| **M5 Submission** | the materials in [`SUBMIT.md`](SUBMIT.md) |

**The M0 gate:** while any M0 ticket is open, only M0 tickets are eligible,
one at a time — everything builds on the skeleton. Until M0 closes, `.github/`
is not a harness path (the scaffold is still being built).

## Filing the backlog

Once the spec PR merges, a plan lap files `backlog-draft.md` as issues (exempt
from the card cap): create the spec's area labels; file tickets in dependency
order, recording each `Dn → #n` in the Now block and rewriting dependencies
before filing; stay idempotent (skip a title already open); apply the ready
label except where an open question blocks; write `backlog filed: UTC` into the
Now block once every draft is mapped. **No ticket is dispatched before that
line exists.** Then post the status.

## Triage, for the event

On top of the build brief's triggers: a milestone's last ticket merging, and
every phase change. The triager also gets `EVENT.md`'s judging criteria and
weights, the latest rehearsal, and the clock, and returns **a demo coverage
map** (each beat: merged, in progress, ticketed or missing) and the biggest
risk to the demo. Rehearse before the triages that close M1, M2 and M4.

## Also needing a decision

Dropping a targeted prize; changing the stack after M0; the demo story.
