# Review: state, routing, labels, merging

Read on a lap that **routes a loop PR, applies a review state, handles a human
action, or merges**. What to tell a reviewer is in [`BRIEFS.md`](BRIEFS.md).
The fixer, the settle bar and the unfixable cases are in [`FIX.md`](FIX.md).

Part of the build brief. See [`README.md`](./README.md).

---

## How routing runs

`harness pr N` reads everything from GitHub and prints the PR's state, the
routing row that matched and **one next action**. `harness prs` does it for
every loop PR. **Do the action it names, then run it again** — several actions
(`reopen`, `post-reopened-human`, `remove-label`) change the state and expect
a second pass. The table below is the definition the CLI implements; if the
two disagree, stop and say so.

## What a PR's state is made of

**Round markers.** Every round posts exactly one PR **comment** whose first
line is one of:

```
cold: findings @ SHA7
cold: clean @ SHA7
semi-cold: closes @ SHA7
semi-cold: does not close @ SHA7
```

- **The marker comes first**, with nothing before it. A one-line summary may
  follow on the same line.
- **`SHA7` is the head the round reviewed**, lowercase.
- **The same round submits one review** (`harness review N`) whose
  body opens with the same marker line and holds the findings. Routing reads
  the comment; fixers and humans read the review. A marker with no matching
  review is a **void** round; a `cold: findings` review with no parseable
  Critical or Medium ID is **malformed**. Either way, spawn a new round.

**Finding IDs**: `R<k>-C<n>`, `R<k>-M<n>`, `R<k>-N<n>`. **`k` is passed to the
round** (`harness pr N` → `nextRoundK`). An ID is raised by the k-th round
review; an earlier ID quoted later is not raised again.

**Fix replies.** A fixer posts one comment: `fix: pushed @ SHA7`,
`fix: no push @ SHA7` or `fix: hold requested @ SHA7`, then one line per open
finding: `R1-C2: fixed` · `declined, needs a decision` · `rejected — reason` ·
`body edited` · `not attempted — reason`.

**Semi-cold verdicts**, one line per open finding in its review:
`R1-C2: closed` · `open` · `open, needs a decision` · `withdrawn`.

**Record comments** (not rounds): `settled: @ SHA7`,
`unsettled: REASON @ SHA7`, `reopened: SHA7 — WHY`, `checks: rerun @ SHA7`,
`checks: none @ SHA7`, `loop: fixer dispatched @ SHA7 — JOB`,
`loop: known limitations @ SHA7`, `loop: answered — …`.

**The open set** is every Critical ever raised on the PR, minus those whose
latest semi-cold verdict is `closed` or `withdrawn` — plus Mediums, under
profile `mediums block: yes`. **The open set, not the marker type, decides
whether anything is open.**

**A Medium does not block** (the default). Before settling a PR that has any,
post one `loop: known limitations @ SHA7` comment listing each open Medium
with its ID, `file:line` and one sentence of consequence, so the cost is on
the PR where a later reader meets it. Do not file a card per Medium. Fix one
only when a fixer is already on the PR for something else and the fix is
genuinely cheap. **Why:** when Mediums blocked, one PR spent seven cold rounds
converging, each finding new ones; there was no bottom to reach.

## Human input

A comment is the human's when it has no machine prefix, its author's
`.user.type` is `User`, and its `.user.login` is this account. Bots post
without prefixes too and are not the human. It is read from issue comments,
review bodies with no round marker, and inline review comments. **Unanswered**
means no `fix:` reply or `loop: answered` comment came after it.

## Routing

Let **L** be the latest round marker, **S** its SHA, **H** the current head.
"Fixers since L" means `— findings` dispatch comments newer than both L and
the latest `reopened:`. Before the table: a hold routes nowhere; a live
terminal state whose SHA ≠ H is [re-opened](#re-opening); a state a human
cleared gets its [`reopened: … human`](#human-actions) marker.

| # | state | next |
|---|---|---|
| 0 | settled at H, not parked | unanswered human → **fix: human**; `.mergeable` false → **fix: conflict**; else the [merge test](#merging) |
| 1 | a `reopened: … — human, was X` newer than L and the latest `fix:` | per [human actions](#human-actions) |
| 2 | no L | **cold** |
| 3 | open set not empty, H ≠ S | **semi-cold** |
| 4 | open, H = S, L semi-cold, no newer `fix:`, every open verdict `open, needs a decision` | **unsettle: needs a decision** |
| 5 | open, H = S, a `fix:` newer than L | **semi-cold**: judge rejections, body edits, declines |
| 6 | open, H = S, no newer `fix:`, fewer than two fixers since L | **fix: findings** |
| 7 | open, two fixers since L, no reply | **unsettle: ran out of rounds** |
| 8 | nothing open, L is `semi-cold: closes` | **cold**: the settling round |
| 9 | nothing open, L cold, H ≠ S | **cold**: new code nobody has read |
| 10 | nothing open, L cold, H = S, unanswered human | **fix: human** |
| 11 | nothing open, L cold, H = S | [**checks**](#checks), then the [settle bar](FIX.md#the-settle-bar) |
| 12 | anything else | **cold** |

**Rows 9–11 take any cold round, `clean` or `findings`.** With nothing open
and L cold, L raised no blocking finding, so a `cold: findings` round with only
Mediums has met the bar. Requiring `cold: clean` sends the PR to row 12, which
finds more Mediums, and it cycles to the ceiling having been finished the
whole time. Under profile `settle rounds: 2`, a PR that never had a finding
takes a second clean cold round at row 11 before the checks.

**Before any round:** capture H and pass H and `k`. When it finishes, read H
again. **If the head moved during the round, the round is void.**

## Conflicts

When `.mergeable` is `false` on a PR that would otherwise settle or merge,
dispatch a fixer to merge the base in. `null` means GitHub is still
computing: try next lap. **Never merge the base into a parked PR.**

## Checks

At row 11, `harness pr N` adds a `checks` action:

- **`settle-bar`**: every run passed (`neutral`, `skipped`, `stale` pass but
  are named in the report — a skipped required job looks green), or the
  pre-CI exception (no workflow on the base yet, no runs on the PR).
- **`wait`**: runs pending, or the base branch red (a PR's CI runs against
  its merge with the base, so it cannot pass while the base fails; once the
  base is green, rerun the PR's failed runs once). Pending is a real race —
  re-read once after a short wait, then leave it unlabelled and name it.
- **`rerun-or-fix`**: a failure. If the latest review says the diff did not
  cause it: `harness rerun RUN_ID` and post `checks: rerun @ SHA7`.
  Otherwise dispatch a check fixer with `harness failed-log RUN_ID`.
- **`fix: check`**, then **`unsettle: ran out of rounds`**: one check fixer
  per head is the cap.
- **`post: checks: none`**: no runs on H. A later lap that still sees none
  dispatches one fixer to find out why CI did not run.

## Terminal states

| state | label (profile `labels: yes`) | record comment |
|---|---|---|
| settled | profile `settled label` | `settled: @ SHA7` |
| unsettled | profile `unsettled label` | `unsettled: REASON @ SHA7` |

**The reason goes in the comment, never in the label.** With `labels: no`,
the latest record comment alone is the state.

| reason | means | cleared by |
|---|---|---|
| `needs a decision` | a finding needs a judgement nobody unattended should make | the human answering, then removing the label (or just answering, without labels) |
| `latched` | a push was refused by something other than a moved head | the human removing the label; one pinned retry |
| `head moved` | a person pushed or rewrote the branch mid-cycle | new commits (the counts reset) |
| `ran out of rounds` | the ceiling, a fix cap or the check cap | the human, or new commits |

When more than one applies, the first in this table wins; name the others in
the comment. **Every state needs a round from this run behind it.** Notify the
human whenever a PR takes `unsettled`. **A PR becoming parked triggers a
triage.**

## Re-opening

A PR whose head differs from the SHA in its live record has new commits
(`harness pr` → `reopen`): post `reopened: SHA7 — commits`, remove the label,
and route again. Its round count and fixer count restart from the new marker.
Act only if no `reopened:` is newer than the record, or every compaction
re-opens it again.

## Human actions

A human clearing a terminal state (removing the label; with `labels: no`,
answering after an `unsettled:` record) shows as `post-reopened-human`. Post
`reopened: SHA7 — human, was LABEL-OR-REASON`, then:

| was | next |
|---|---|
| settled | **cold round**: the human wants another look |
| `needs a decision` | **fix**, handing the fixer every human comment since the record |
| `latched` | **fix**: retry the pinned push once; if refused again, re-apply `latched` and say so |
| `ran out of rounds`, `head moved` | route normally, counts restarted |

## Merging

The merge step runs on lap item 3 and on notification turns after the base's
checks have completed. **At most one merge per lap or turn.** `harness
merge-check N` evaluates every item it can, fresh; two stay yours:

- **the clock allows it** (a pack may restrict what merges when)
- **nothing has merged since the base branch's checks last completed**

It checks: `merge policy: auto`; the base green; settled at this head; no
hold; no harness or high-risk path; nothing open and no unanswered human; the
checks pass; `.mergeable` true. When `ok`, and the two items above are
yours to vouch for:

```
harness merge N --sha FULL_SHA
```

It runs the merge test again and refuses unless it passes at exactly that
head, marks a draft ready, squash-merges with the head pinned (a moved head
refuses), deletes the branch through the API (never a local branch a worktree
may hold), and reads `.merged` back. **In a cloud session**, marking a draft
ready needs GraphQL, which the session cannot reach: `harness merge` stops
and names the GitHub MCP tool (`update_pull_request`, `draft: false`) to use
first; read the PR back before retrying. Then confirm
the linked ticket closed. Log it.

**Otherwise settled PRs are parked**: listed in the status as ready to merge,
with any ship verdicts, and the loop goes no further.
