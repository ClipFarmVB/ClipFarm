# Reviewer briefs — what to tell a round

Read on a lap that **spawns a review round**, cold or semi-cold. Everything a
subagent needs to be told is here; the selection and routing that decide *which*
round to spawn are in [`REVIEW.md`](./REVIEW.md).

Split out of `REVIEW.md` (CF-365) because a **step 2** lap spawns semi-cold
rounds and needed the whole of that file — 18k tokens — to reach one section.
A lap that only selects, or only re-reads checks, now skips this too.

Part of the unattended-run brief — see [`README.md`](./README.md).

| file | when to read it |
|---|---|
| [`START.md`](./START.md) | once, at the start of a run |
| [`RULES.md`](./RULES.md) | **every iteration** |
| [`REVIEW.md`](./REVIEW.md) | a lap that reviews a PR |
| [`BRIEFS.md`](./BRIEFS.md) | a lap that spawns a round, cold or semi-cold |
| [`FIX.md`](./FIX.md) | a lap that fixes findings |
| [`TICKETS.md`](./TICKETS.md) | a lap that implements a ticket, or a card to file |
| [`REPORTING.md`](./REPORTING.md) | end of the run |
| [`RATIONALE.md`](./RATIONALE.md) | optional background |

---

### Cold and semi-cold rounds

**Never review from this session. Spawn a subagent and let it review cold.**
The session that wrote the code is the most anchored possible reviewer: once it
has judged a file fine it checks the delta rather than re-deriving that
judgement, so everything already blessed becomes invisible. A long context also
spends attention on conversation history that a cold reviewer spends entirely on
the diff. Clearing the context is not a retry — it produces a *different
reviewer*, which is why a fresh pass keeps finding real things after both sides
agreed a PR was ready. In this loop one session writes the code, opens the PR,
reviews it and fixes it; nothing in that chain is cold unless you make it so.

**There are two kinds of round, and they do different jobs.**

A **cold** round gets the PR number and nothing about how the diff came to be:
no plan, no reasoning, no summary of what was built, no earlier findings. It
reads the repository itself. Re-deriving that context is what a subagent
normally costs you; here that cost is the point. Step 2 of
[Working a ticket](TICKETS.md#working-a-ticket) already spawns one to cross-check plans —
same mechanism, pointed at a diff. **The first round on a PR is always cold**,
and so is the round that awards `review-settled`.

A **semi-cold** round is for checking a fix — **whoever pushed it.** Usually
that is this run; on another account's branch, it is the author responding to
the review, and that case has to work or a `unsettled: not our branch` PR could
never satisfy the settle bar and would burn to the ceiling on every visit. It
gets the finding it is checking, the commits that landed since the marker that
raised it, and any reply on the thread — and it is asked to judge whether that
finding is actually closed, then review the new head for anything the change
introduced. It is anchored by construction: it will check the delta rather than
re-derive the whole diff. That is the trade, and it buys the one thing a cold
round cannot do — someone other than the author confirming the fix does what
the finding asked.
### The semi-cold reviewer's brief


**It posts one marker comment like every other round** — body starting with the
literal `semi-cold: closes @ <sha>` or `semi-cold: does not close @ <sha>`,
carrying the same head SHA the cold brief specifies, before any heading, and
nothing after it but an optional one-line summary. Each finding it checked, with
the reason, and anything the fix introduced, goes in its **review**, tiered as
below — not in the comment. Tell it, as you tell the cold reviewer, to post the
marker with `gh pr comment` — never `gh pr review`, never
`/code-review --comment` — and then to submit that write-up as a review with
`gh pr review <n> --comment --body "…"`, **opening the review body with the same
marker line** so step 2 can tell it from a human's review. One
*comment* per round, never one per finding: the rules that recover state after a
compaction count marker comments, and a round posting several inflates that
count as surely as one posting none. The review is not a comment and is not
counted; it is what makes the round visible as work.

**Check the marker landed, every time.** Immediately after a round posts, read
the latest marker back and confirm it is the one that round wrote, with the SHA
it reviewed. A round whose marker was posted as a review, prefixed with a
heading, or phrased outside the four forms is invisible to every rule here, and
the two bookkeeping sources — your logged round count and the marker count on
the PR — then disagree with nothing to say which is right. If it did not land,
re-post it correctly; that repost is not a new round and does not spend budget.

**Measure the commits to check from the marker that raised the finding, never
from a later `cold: clean`.** Where a clean round was posted over a still-open
finding, that clean marker matching the head says only that nothing has landed
since *it* — the finding may be several commits older, and a fix may sit between
the two. That fix is precisely what the round exists to check. Reading emptiness
off the clean marker instead would write `does not close` against already-fixed
code, for the reason
[the routing table](REVIEW.md#routing-what-the-marker-tells-the-run-to-do-next) gives
under its open-finding rows.

**If a round is ever dispatched with nothing to check, write `does not close`.**
Routing does not send one: the open-finding rows split on whether anything
landed since the finding's marker, and the empty case goes to step 2 without
spending a round. This is the answer if some other path produces one anyway —
nothing landed, so the finding is unfixed by definition. Do not improvise a
verdict from an empty diff.

**A "does not close" verdict leaves the finding open.** Fix it again and take
another semi-cold round, or, if you cannot, apply the `unsettled` label with a
comment naming the reason that fits — any of the four — record it, and move on.
It does not become closed by being argued with.

**Never let a semi-cold round settle a PR.** It was handed the previous
reviewer's conclusions, so its silence inherits their blind spots; treating that
as the terminal state is the anchored judgement stamped final, one remove away.

**It is cold to this session, not to the PR.** Given a number it will read the
body you wrote and every thread on it, including the "what changed" replies step
2 requires — so your framing reaches it anyway, through GitHub rather than
through the prompt. That leak is not fully closable while reviews happen on a
PR. Narrow it: write PR bodies and thread replies to state *what changed*, not
to argue the change is right. A body that pre-empts objections anchors every
reviewer who ever reads it, cold or not.

What "cold" does **not** withhold is how to do the job. Give it the review
instructions below in full, plus the rules that bind it as an agent: **no
attribution stamps** on its comment, never push, never merge, never deploy,
never echo a credential. Pass those rules explicitly — do **not** hand it the
[Hard rules](RULES.md#hard-rules) section wholesale, which would tell the reviewer you
just spawned that it is never the reviewer. A subagent in a sandbox inherits
none of your local settings, so the no-stamp rule has to travel with it or its
review arrives signed.

**Capture the head SHA yourself, before spawning, and pass it in.** Do not let
the reviewer fetch its own: a push can land while the round is running — from
another account at `all` scope, or from a concurrent process — and it would then
stamp a SHA it never read — after which the SHA test reports "same" and the new
code is never looked at. Read it once, hand it over, and **re-read it when the
round finishes**: if the head moved during the round, that round is void. It
does not count against the ceiling, and the PR needs a fresh one against the new
head.

### The cold reviewer's brief

Its brief: run `/code-review high <PR number>` — **naming the number, not
describing the PR** — then post one marker comment and submit its findings as a
review. The bare form has been observed reviewing `main`'s tip commit instead of
the PR — three sightings of seven — apparently by discovering a diff for itself
and, finding every local one empty, falling back to `HEAD~1..HEAD`. **That
mechanism is a suspect, not a cause**: the obvious version of it, "the branch is
already pushed so the diff is empty", was tested and refuted — sightings that
shared exactly that state targeted correctly. What the record does support is
narrower: every round that recorded passing the number targeted correctly, and
every round that recorded the skill finding its own diff did not. Naming the
number is cheap and consistent with that; it is not yet known to be the fix. Give it the head SHA you captured, and require
the comment's body to **start** with the literal `cold: findings @ <sha>` or
`cold: clean @ <sha>`, before any heading or
formatting, since that whole line is what selection matches and routes on.

**Which of the two is decided by Critical and Medium alone.** `cold: findings`
means at least one Critical or Medium. A round that found *only* nits, or
nothing at all, writes `cold: clean` — the settle bar permits nits, and a round
that reports one as `cold: findings` routes the PR to a fix it does not need,
which on another account's branch ends as `unsettled` on a PR that had actually
cleared the bar. The nits go in the review, as they would for any other round;
the comment stays a marker line either way.

A summary may follow on the same line (`cold: findings @ 2c1a865 — 2 Critical,
1 Medium`); nothing may precede the
marker, and the SHA is not optional — the routing table is keyed on it, and a
marker without one can never match a head.

**That line is the whole comment.** The findings do not go in it — they go in
the review, tiered:

- **Critical** — correctness, security, data loss
- **Medium** — should fix before merge
- **Nit** — style, naming, comments

**Point the round at the diff's own prose, explicitly.** A frequent defect this
loop produces is not broken code — it is a sentence in the diff that the diff
makes false. In the run of 2026-08-30 it accounted for **both** Criticals and
for most Mediums: on #438, four of the five. Two representative instances, both
raised by first rounds: a docstring listing the one call form the pattern it
described could already see, and a comment citing 13 beside an assertion that
measures 11 — which had set a constant from the wrong number.

**It is not the whole risk surface, and the round should not be told it is.**
The decisive finding on #438 was a code regression a mutation caught — a
quote-anchored pattern that made `'numpy==1.26.4'` invisible — with no prose
involved. Prose-checking is an *addition* to executing the code, never a
substitute.

It needs saying separately because reading the code does not catch it: a
reviewer checking whether the code is correct passes over a comment that
describes it wrongly. The round has to be told to check each claim in a
comment, docstring, commit message and PR body **against what the code does**.
**Do that part yourself.** No sighting so far records the skill reading a PR
body, and the one body-shaped finding it produced was anchored in a file
instead — so running it does not discharge this paragraph. Stated as what the
record shows rather than as never: the rounds logged findings, not the absence
of them, so "it has never read one" is more than the evidence carries.

Three sub-cases, each of which cost a round:

- **A correction deserves the same scrutiny as the claim it replaces.** One
  "fix" that run replaced an accurate sentence with a false one, and it took a
  round reading the primary source to establish the original was right.
- **A tally goes stale the moment a row is added**, and the reader checks the
  tally rather than the rows — so prefer naming the rows. But de-counting is not
  free either: replacing "three of the failures are silent" with "each failure
  below is silent" traded a stale number for a false universal.
- **An enumeration in one file goes stale when another file grows.** Grep for
  every copy of a claim before calling it fixed.

**Post the marker with `gh pr comment`. Not `gh pr review`, and not
`/code-review --comment`.** This belongs in the brief you hand over, not only in
the selection rules above, because the skill you just told it to run documents
`--comment` as its own way to publish findings — inline review comments, which
is the move a reviewer holding the skill reaches for first. Neither shows up as
a comment in the REST comments listing that selection reads, so either one
strands the PR on its previous marker.

**The ban is on the marker, and it still stands.** A review is now required
*as well*, but it is submitted deliberately with `gh pr review --comment` after
the marker is posted — not by letting `/code-review --comment` publish inline
comments in place of either artifact.

**Then submit the findings as a review**, `gh pr review <n> --comment
--body "…"`, **opening the review body with the same marker line** and putting
the tiered findings under it. Both artifacts, every round, and the findings
appear in exactly one of them: the marker comment is what routing reads back,
the review is what a human reads, what step 2 retrieves the findings from, and
what the contribution graph counts. The repeated marker line is how step 2 finds
the right review — a maintainer's review carries none, and is otherwise
indistinguishable. Self-reviews count too, which matters because most of what this run
opens it will also review.

Challenge the design where warranted, not only the code; say so when a premise
looks wrong. **Verify claims against the repository** rather than trusting the PR
description — that has caught real errors here more than once. Never mark a
finding confirmed without checking it.

**Executing a claim is necessary and not sufficient — the inputs have to be able
to disprove it.** The worst error of the third run was a comment calling a live
branch unreachable, "verified" by running two values that were *both blank after
stripping*. Both shared the property under test, so neither could have
disproved the claim, and the real way in (`CORS_ORIGINS=","`) was already listed
in that same file and already pinned by a passing test. Before believing a
check: ask which input would make the claim false, and confirm your set contains
it. A green suite proves nothing until a control mutation shows the harness can
go red at all.

**Write the prose claiming a gap is closed *after* running the mutation, never
before.** In one run the fix for "this guard can pass without checking"
contained a smaller instance of itself three times on a single PR: a false
reason recorded for a launch flag, a guarded import that swallowed the
blocker's signal, and a premise test that launched a *different child* than the
one it was a premise about — so the flag it existed to forbid left every test
green. Each was caught by running the mutation; each had its explanatory comment
written first. The comment is not evidence, and writing it first is what makes
it feel like evidence.

Two shapes worth knowing by name:

- **A loop assertion with no length guard passes on an empty iterable.** Two
  cases written to catch a missing CSS class iterated a helper that located
  elements *by a string another test pins*; changing that string emptied the
  list and both cases went green while both couplings were broken. Assert the
  count before iterating.
- **A premise test must run under the same launch path as the thing it is a
  premise about.** The third instance above is this shape: two argv literals, so
  the test vouching for the child was vouching for a child that did not exist.
  Adding an isolation flag to the child under test left every case green; one
  shared helper made that same mutation red.

  Stated narrowly on purpose. The first version of this lesson said the flag
  made the tests *vacuous*, and measurement said otherwise — the blocker still
  fires, and a control matching on its message still discriminates. What the
  flag costs is that the blocker becomes *redundant*, which is a different
  failure and the only one the evidence supports.

**Ask what the repository already asserts before deriving anything.** Six times
in one run the answer was already written down — in another line of the same
file, in an existing test, or in the installed package's own source. Reading it
is cheaper and more reliable than re-deriving it, and a file that contradicts
itself is itself the finding.

**Check claims about anything outside the diff, and check them again at settle
time.** The costliest class here is not a claim that was wrong when written — it
is one that was *right* when written and went false while the PR sat open. One
PR body carried six: a test count, a file that had since been renamed on another
branch, a sibling PR described in the present tense that had not landed, and a
pre-commit behaviour that `main` had since replaced. None was careless; nothing
re-checked them. So before settling a PR, list every assertion it makes about
something it does not itself contain — another branch, another PR, `main`, a
tool's behaviour, a hook — and re-verify each against current state. This is a
grep and a handful of reads, not a review round, and it is where the most
valuable finding of the third run came from: a merge note that told the next
person which checks to keep from a parallel branch stack, and named the wrong
ones.

**The reviewer runs on the same model as this session.** A spawned agent takes
its model from its definition's frontmatter when it has one, and only inherits
the parent's otherwise — so a definition added later can quietly review at a
different, weaker model with nothing in this brief to notice. There is no
`.claude/agents/` directory in this repo today, so inheritance is what happens;
if that changes, or if you spawn a type that pins its own model, **say so in the
report**. A review's depth is not something that should vary by accident.

**Pin the level explicitly.** `/code-review` reuses the level you last typed,
and a freshly spawned subagent has no last level — leaving it off makes the
depth of every review an accident of the harness. `high` is the level that
matches this brief: broader coverage, some uncertain findings included.

Do not use `/code-review ultra`. Whether or not it counts as an effort level
alongside `low`…`max`, it launches a multi-agent review in the cloud, is billed
separately and is user-triggered — none of which an unattended run should reach
for.

**Check what it reviewed before using any of it.** Compare the files it names,
and the commit its own scope paragraph cites, against the PR's diff. On a
mismatch, discard the output and review by hand — and say so in the review, so
the next round knows the verdict is the round's own work. Three of seven
sightings reviewed `main`'s tip commit rather than the PR, returning findings
that were internally consistent and about the wrong change; on two of the three
the wrong commit touched the same files as the PR, which is what makes this
worth a check rather than a glance.

**Open a cited location before repeating it.** Twice the skill has pointed
somewhere real but wrong: once anchoring a finding about the PR *description* at
the nearest thematically-related file text, and once rooting every path at the
main checkout while the line numbers were the head's. That second one is the
dangerous shape, because **the path usually resolves**: you land in a real file
that simply does not contain what the finding describes, or past its end. It
reads like a stale finding rather than a broken reference, and confirming that
the file exists is not the check. Open the location in the worktree you are
reviewing and read what is actually there. A finding you repeat in a marker, a
review or a card carries its location as your claim, not the skill's.

And "the skill agreed" is not verification of a number. It once confirmed a
figure by recomputing it the way the diff's author had, rather than from the
basis the diff stated — which is the one check that would have failed it.
