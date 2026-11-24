# Reviewer briefs

Read on a lap that **spawns a review round** (cold or semi-cold) or a ship
review. Which round to spawn is decided by [routing](REVIEW.md#routing).

Part of the build brief. See [`README.md`](./README.md).

---

## Why rounds are subagents

The session that dispatched the code is the most anchored reviewer there is:
once it has judged a file fine, the file stops being visible to it. A fresh
subagent is a *different* reviewer. It runs on the same model with the same
priors, so it finds different things rather than all things, and the cycle
depends on that difference.

- **A cold round** gets the PR number, the head SHA and this brief, and nothing
  about how the diff came to be: no plan, no reasoning, no summary of what was
  built, no earlier findings. Re-deriving that context is what a subagent
  normally costs you; here that cost is the point. The first round on a PR is
  always cold, and so is the round that settles it.
- **A semi-cold round** checks fixes. It gets the open set, the fixer's reply,
  any human comments, and the commits since the round that raised each
  finding. In exchange for being anchored, it can answer what a cold round
  cannot: *is this finding closed?*

**Both are cold to this session, not to the PR.** They will read the PR body
and its threads, so those should say what changed and never argue that the
change is right.

## What every round is told

- **Context**: the product and what makes work valuable here, from the
  profile's prose. Its primary path, so defects there weigh most.
- **Where to work**: its own worktree, detached at the SHA you captured
  ([dispatching](RULES.md#dispatching)). **It never fetches its own head** — a
  push during the round would otherwise go unreviewed. Its own slot and `PORT`.
- **The current checks.** If one is red, the review says whether the diff
  caused it.
- **`k`**, its round number, for finding IDs.
- **How to post**: write each body to a file with a quoted heredoc
  (`cat > FILE <<'EOF'`), then post it with the harness CLI (its path is
  passed in: `node PLUGIN/bin/harness.mjs`). Never `gh`: a cloud session has
  none, and the CLI works in both.
  1. **one comment** (`harness comment N --body-file FILE`) whose body starts
     with the literal marker at that SHA
     ([markers](REVIEW.md#what-a-prs-state-is-made-of))
  2. **one review** (`harness review N --body-file FILE`) whose body opens
     with the same marker line, then lists the findings, each with its ID
  3. **never** `/code-review --comment`, and no other comments

  A clean round still submits a review, saying what it checked.
- **The rules that bind it**: never push, merge or deploy; never echo a
  secret; no attribution stamps; on a rate limit, stop and report it.
- **The profile's high-risk definition**, if it has one: a change that meets
  it is a Critical tagged `high-risk`, which makes it a decision for a human.

**Tiers:**

| tier | meaning |
|---|---|
| **Critical** | wrong behaviour, data loss, a security hole, a broken primary path, a false claim in the diff, or a high-risk change under the profile's definition |
| **Medium** | a real defect someone pays for later: a missed case, a test that passes for the wrong reason, an acceptance criterion not met, an objective visible defect on a primary-path screen (per the `ui-craft` rubric) |
| **Nit** | style, naming, wording, taste, polish off the primary path. Never blocks. |

**Anchor every finding to `file:line` at the reviewed SHA, and open that
location in the reviewed worktree before citing it.** The code-review skill
has rooted paths at the main checkout with line numbers from the head, and a
path that resolves is not proof the file says what the finding claims.

## The cold reviewer's brief

**Run `/code-review high N`, naming the PR number.** Left to find a diff by
itself, it has reviewed the base branch's tip instead. Check that the files it
names are in the PR's diff; if not, or if the skill cannot run, review by hand
and say so. **Never `/code-review ultra`.**

The skill's output is a starting point. The round also covers:

- **Acceptance.** Read the linked ticket and check every acceptance criterion
  against the code and the tests. An unmet criterion is a Medium.
- **Prose against behaviour.** Claims in comments, docstrings, commit
  messages and the PR body, against what the code does. A sentence the diff
  makes false is one of the commonest defects a loop produces.
- **Tests that cannot fail.** Where a test looks inadequate, mutate the code
  it covers and show the test still passes. Assert the mutation's anchor
  matched exactly once; restore by copying, and verify with `cmp`; clear caches
  that fake survivals. A mutation that fails for an unrelated reason proves
  nothing, and a loop assertion over a possibly empty list passes vacuously.
- **Inputs that could disprove the claim.** For every "verified", ask which
  input would make it false, and include it. A green suite proves nothing
  until a control shows it can go red. A premise test must run under the same
  launch path as the thing it vouches for.
- **Claims about things outside the diff** (another PR, the base branch, a
  tool's behaviour), checked against their current state.
- **What the repo already asserts.** Look for an existing test, doc or
  config that settles a question before deriving it.
- **Design.** Whether the change solves the ticket's actual problem, and
  whether a simpler shape exists. Kept separate from the findings.
- **Anything visible.** Run the app and apply the `ui-craft` rubric to every
  screen the diff touches, from screenshots taken in this round.

**Write a sentence claiming a gap is closed only after running the check that
closes it.** "The skill agreed" is not verification.

## The semi-cold reviewer's brief

**It gets:** the open set — the blocking findings only (`harness pr N` →
`blocking`), each ID and its text from the round review that raised it;
Mediums are not in it and get no verdict unless profile `mediums block: yes`.
Also the fixer's latest `fix:` reply; every human comment since the latest
round; and **the commit range from the marker that raised each finding** (not
from a later clean round) to the captured head.

**For every finding in the open set, one verdict line:** `closed` (the code,
tests or body now resolve it — checked against the code, not the reply);
`open`; `open, needs a decision` (it agrees a human must decide); or
`withdrawn` (it agrees with the fixer's rejection, for a stated reason).

**Then it reviews the commit range** for anything new at Critical or Medium,
with new IDs. A new Medium is raised so it can be recorded; only a new
blocking finding reopens the PR.

**Its marker** is `semi-cold: closes @ SHA7` only if every verdict is `closed`
or `withdrawn` and no new blocking finding was raised — a new Medium does not
stop it closing. Otherwise `does not close`. Given nothing to check against,
it writes `does not close`.

- **An empty commit range is normal** after `fix: no push`. Judge the
  rejections and declines on their merits; a finding that needed a code change
  stays `open`.
- **A semi-cold round never settles a PR.** It inherited the previous
  reviewer's conclusions, so its silence inherits their blind spots.

## Ship reviewers

With profile `ship reviewers` set, the [settle bar](FIX.md#the-settle-bar)'s
last item is one SHIP verdict per named reviewer **at the current head**
(`harness pr N` → `settleBar.shipNeeded` names who is missing). Spawn them only
once everything else on the bar holds. Each gets the cold brief above and is
asked one question: ship this exact head, or not?

- **`sonnet`**: a subagent spawned with `model: sonnet`.
- **`sol`**: `codex exec -m gpt-5.6-sol` (fast tier, high effort), run in the
  reviewer's own worktree at the head, with the brief on stdin. If `codex` is
  missing or fails, say so and apply `unsettled: needs a decision`; never post
  a verdict it did not give.

**A SHIP** posts one comment: `ship: REVIEWER SHIP @ SHA7`, with what it
checked below. It is not a round.

**A NO-SHIP posts as a cold round**, because its reasons are findings: the
comment `cold: findings @ SHA7` with `ship: REVIEWER NO-SHIP` after the marker
on the same line, and a review whose blocking reasons are Criticals with IDs
(`R<k>-C<n>`). Routing then sends the PR to a fixer like any other finding,
and the new head needs every ship verdict again.

**The loop never counts a ship verdict it posted as a human's approval**, and
never merges on ship verdicts alone.
