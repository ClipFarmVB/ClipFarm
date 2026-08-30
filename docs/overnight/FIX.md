# Step 2 — fixing what a round found

Read on a lap that **fixes findings**: the cycle, the settle bar, and how to choose an `unsettled` reason when a finding cannot be fixed.

Part of the unattended-run brief — see [`README.md`](./README.md).

| file | when to read it |
|---|---|
| [`START.md`](./START.md) | once, at the start of a run |
| [`RULES.md`](./RULES.md) | **every iteration** |
| [`REVIEW.md`](./REVIEW.md) | a lap that reviews a PR |
| [`FIX.md`](./FIX.md) | a lap that fixes findings |
| [`TICKETS.md`](./TICKETS.md) | a lap that implements a ticket, or a card to file |
| [`REPORTING.md`](./REPORTING.md) | end of the run |
| [`RATIONALE.md`](./RATIONALE.md) | optional background |

---

#### Step 2 — fix what the round found

**2 — Address the review findings on the PR you are carrying**, if this account
opened it. That is the same test the `review scope` filter runs —
`gh api user --jq ".login"` against the PR's `.user.login` — so at
`review scope: own` it is true of every PR in the queue. See
[The push test](RULES.md#the-push-test).

So step 2 applies to the whole of an `own` queue.

*It applied to "most of" one until 2026-08-29*, when the push test also read
commit authorship. That condition is gone, and the paragraph it justified with
it: a branch carrying someone else's commits is now pushable, and whether to
push to it is a judgement the run makes by reading the PR rather than a gate a
query answers. Where a conversation about work in progress is live, write a
comment instead — and say in the report that you chose to.

For a PR you may not push to, describe the fix in a comment and apply
`unsettled` with the reason that fits:

- **Another account's PR** — `unsettled: not our branch @ <sha>`. Only reaches
  the queue at `review scope: all`. A cheap terminal state, not a dead end: the
  author's next push re-opens it.
- **A push the harness refuses** — `unsettled: latched @ <sha>`. Can arise at
  either scope. **Commits do not re-open this one**, deliberately, and no run can
  clear it: a human handles the PR outside the loop. **Report it by name and say
  exactly what refused the push**, plus the fact that the findings are written up
  in the review. The report is the only place a latched PR is visible; without
  that line it sits outside the loop with nobody aware.

  **Unless a finding also needs a judgement — then `needs a decision` wins**,
  per the precedence under [the terminal labels and their
  reasons](REVIEW.md#the-terminal-labels-and-their-reasons). The two want different
  things from different people: a judgement call needs the reviewer's question
  answered, a latch needs someone to unblock the push machinery.

**When the thing you are fixing is a wrong claim, grep for every copy of it
before calling it fixed.** A "one-line" documentation fix turned out to be three
lines in three files, and the copy that mattered most — the onboarding path a
fresh clone runs first — was the one left untouched, so the repo then stated two
different things about one problem in two places. Correcting one instance of a
claim is not a smaller version of correcting it; it can be worse than leaving all
of them, because the surviving copies now have a contradicting neighbour to be
read against.

**Read the findings out of the round's review.** The marker comment carries the
prefix, the SHA and at most a count; the findings are in the review that round
submitted.

**Which is why the review body opens with the same marker line** — the marker,
then the findings. Not a second copy of the findings: one line, and it is what
makes the review identifiable at all. Human reviews on this repo carry no marker
(the existing one on #278 opens `### Critical`), and there is nothing else to
tell them apart: a round's review and a maintainer's are both `state:
"COMMENTED"`, and on this repo they are posted by the **same login**, so
filtering by author would match both. Without the marker line, `tail -1` can
hand step 2 a maintainer's review to "fix".

It also solves the SHA. The review object's own `.commit_id` is the reviewed
head, but it returns all forty characters and would have to be sliced to
`[0:7]` to compare against anything else here; the marker line already carries
the seven-character form every other rule uses.

```
ROUNDS='^(cold: (findings|clean)|semi-cold: (closes|does not close)) @ ?[0-9a-f]{7}'
RID=$(gh api --paginate repos/ClipFarmVB/ClipFarm/pulls/<n>/reviews --jq ".[] | select(.body | test(\"$ROUNDS\"; \"i\")) | .id" | tail -1)
[ -n "$RID" ] || { echo "no round review — see the transitional note below"; exit 1; }
gh api repos/ClipFarmVB/ClipFarm/pulls/<n>/reviews/$RID --jq ".body"
```

**Take the id first, then fetch the body.** Reviews come back oldest-first, so
`tail -1` over the *ids* is the newest round review — but piping bodies through
`tail -1` returns the last **line** of the last body, because a findings
write-up is many lines. Ids are one line each, which is what makes the two-step
correct. The guard **exits**: an unset `RID` requests `…/reviews/`, which 404s,
and a guard that only prints lets the request it detected go out anyway.

Match the review to the marker comment by the SHA both carry: a review whose
marker names an earlier head describes findings on code that has since changed.

**Transitional: PRs already mid-cycle when this landed have no round review at
all.** Their findings were posted in the marker comment under the previous
shape.

The test is the one the command already performs, and it evaluates itself — no
date to look up: **a PR with a marker comment but no matching round review is
one of these.** Read its findings from the marker comment. Every round posted
after this landed leaves both artifacts, so the case disappears on its own as
the queue turns over. Do not conclude a PR has no findings because it has no
round review.

If a fix needs no human decision, implement it, push to that PR's branch, and
reply on the thread saying what changed. If it needs a judgement call, log it
and leave it.

##### The cycle and the settle bar

**This is a cycle, and the order matters.** A cold subagent posts the first
review. *Then* you push the fix and reply saying what changed. *Then* a
**semi-cold** subagent checks that fix against the finding it claims to close.
*Then*, once nothing is left open, a fresh **cold** round decides whether the PR
settles.

**A cold round's silence is not evidence a fix landed.** A round that does not
re-raise a finding is exactly what you would expect from a reviewer that found
*different* things this time — which the closing paragraph says is the norm.
That is why fixes are closed by a semi-cold round that was pointed at them, and
why the settle verdict still needs a cold one.

**Never describe a fix inside the review that found it.** Two of the five PRs in
the second run did exactly that — found something, fixed it, and posted one
review narrating both. Nothing independently looked at the fix, and because the
fix commit predated the review, step 1 saw no commits after it and skipped the
PR forever. The next round is not ceremony: it is a fresh reviewer over code
that changed *after* the previous one formed its judgement.

**Cycle until a cold round produces no Critical and no Medium finding.** Not a
fixed number of rounds: each cold round is a new subagent reading the diff
without the last one's conclusions, so it is a genuinely different pass, and
stopping at two stops while that is still paying. Nits may remain — requiring
zero findings would review forever.

**When a cold round clears that bar — and the checks have passed, below — label the PR `review-settled` and post a
`settled: @ <sha>` comment with it.** That is the terminal state, and it is what
stops future runs re-reviewing finished work. The comment carries the SHA the
carve-out compares against, and it is the only trace left if a human later
removes the label — without it, a maintainer asking for another look gets the
label silently re-applied with no rounds run.

**The bar spans every round, not just the last one.** Settle only when the most
recent **cold** round raised no Critical or Medium finding **and** every Critical
and Medium raised in any earlier round — by a cold reviewer or by a semi-cold one
reviewing a fix — has been closed by a semi-cold check.

A quiet round on top of an unfixed Critical is a reviewer looking elsewhere, and
treating it as the terminal state buries the finding under a label that stops
anyone looking again.

**A PR that has never had a finding needs two `cold: clean` markers in a row,
not one.** Everywhere else, settling rests on something a reviewer confirmed: a
semi-cold round said this fix closes this finding. A PR clean on its first look
has no such confirmation anywhere — one reviewer's silence would carry the
whole terminal state, and silence is the evidence this document rejects
everywhere else. Two independent cold passes is the weakest confirmation
available when there is nothing concrete to check, and it is the least that
label should cost.

The gap between those two rounds is a mid-cycle window like any other: no
commit, no label, nothing for the timestamp test to see. The first round's
`cold: clean` marker is what closes it — a PR sitting on one of those, with no
`review-settled` label and no finding in its history, is waiting for its second
round. Prefer to run both inside the same carry, so the window never opens.

**Only a cold reviewer's verdict earns the label** — never this session's, and
never a semi-cold round's.

**Do not fix nits between the settling round and the label.** That commit would
land after the round that blessed it and before the label, so the "commits since
the label" test never sees it: the label would certify a head no reviewer has
looked at, and the PR would be skipped forever. This is the same shape as the
mistake in the second run, described two paragraphs down.

So: **fix nits before the settling round, or leave them** — and once nothing
Critical or Medium is open, leaving them is the only option, per the freeze
below. Do not push a nit fix after the label either — it re-opens the PR by
design, which buys another cold round, which can surface another nit, which
presents the same choice again. A PR can cycle indefinitely on nits alone,
spending budget every lap, and nits are what the settle bar deliberately
tolerates. Leaving one is the terminating move; file a card if it is worth more
than that.

**Freeze the head once nothing Critical or Medium is open.** The rule above
still leaves one loop, and the third run walked straight into it: a semi-cold
round closed the last Medium and raised a nit, the nit was fixed *before* the
settling round (which the rule permits), the new SHA needed a fresh cold round,
and that round found new nits — three times over, until the budget ran out and a
converged PR was labelled `unsettled: ran out of rounds`. Nothing was wrong with
any single step. So: **from the moment a round reports no Critical and no Medium
outstanding, only a Critical or Medium may change that head.** Nits found from
then on go to a card, however cheap they look. "Cheap" is what makes this loop
attractive on every lap.

**Never settle over a check that has not passed.** The settle bar is about what a
reviewer found; this is about whether the code runs, and a green review over a
red suite is the failure CF-275 was filed for: six rounds on #307 while every CI
run failed, fixed only when a human noticed. Do not apply `review-settled` while
any check run on the head SHA is in **any** of these states:

- concluded `failure`, `timed_out`, `cancelled` or `action_required`;
- **not completed** — `queued` or `in_progress`, where `conclusion` is still
  `null`. This is a state, not an absence, and it must be named: a rule that
  lists only failing conclusions permits settling over a suite that has not
  spoken yet, which is CF-275's shape exactly rather than a milder version of it;
- or the head has **no check runs at all** — `total_count: 0`. `ci.yml` is
  `on: pull_request` with no path filter, so every PR gets every job; nothing
  means the workflow never fired, not that it passed quietly.

`neutral`, `skipped` and `stale` do not block. Say in the report if one appears,
because a skipped required job and a passing one are the same green to everything
downstream.

How to read them, and the two endpoints that look right and are not, is
[in step 1](REVIEW.md#reading-the-checks-before-a-round-and-again-before-settling).

**Pending is the ordinary case, not an edge case.** A carry pushes a fix and
reaches the settle bar within a minute or two, while CI takes longer than that.
Re-read once after a short wait; if it is still running, the PR is simply not
settleable yet this lap. That is not a finding, not a label, and not a reason to
spend a round.

**Leave such a PR unlabelled, and name it in the report.** It needs no new
`unsettled` reason and must not be given one: `unsettled` records open Critical
or Medium findings that a round put there, and a PR that reviewed clean has none
— the label would assert something false, and no carve-out could clear it, since
a re-run going green moves no SHA for the [carve-out test](REVIEW.md#record-comments-human-removal-and-re-opening)
to see. Unlabelled already means unfinished. The exit is clean: when CI goes
green the head has not moved, so the head-matching `cold: clean` markers still
stand and the next run settles it **spending no round at all**.

**The cost, so it is not discovered instead of decided:** such a PR re-enters the
queue every night until its CI goes green, and each visit costs the read above.
**Nothing bounds that.** The per-PR ceiling counts rounds within one run and this
visit spends none, so it charges nothing; the recurrence is across runs, which no
counter here windows. It ends when CI goes green, a human acts, or the PR is
closed — and until then the report bullet is what makes it visible each night.

That is the price of not inventing a label with no way out. The brief has been
here before from the other side: [CF-274](RULES.md#hard-rules) removed the grant
mechanism that had been added to give such a label an exit, which is why the
answer this time is not to create the label.

**This is the one thing that may move a frozen head.** The rule above says only a
Critical or Medium may change the head once nothing is open. A red check is
neither, so taken literally a converged-but-red PR could neither take the commit
that would turn it green nor ever settle — no exit at all. It may take that
commit. Nothing else about the freeze relaxes: the fix goes in, the head moves,
and the PR takes the fresh cold round the routing table calls for.

**A finding against the PR body is not a finding against the head.** It still
has to be fixed — a body is editable and a body edit is not a commit, so it
changes no SHA and disturbs no marker — but it must not block settling, and it
must not spend a round. The same run ended with a cleared codebase and an
`unsettled` label because the only surviving Medium was in the PR description.
The code at that head had been reviewed and found clean; the label said
otherwise. Judge the head on the head. Fix the body, say in the settle comment
that you did, and settle.

Two things that carve-out has to be explicit about. **It overrides the settle
bar's "closed by a semi-cold check" for this one case**, because that bar is
about findings against the head and a body carries none — requiring a round to
check a description would spend the budget the carve-out exists to save.
And **confirm the body fix actually landed by reading the body back**, the same
way a label is verified: an update call reports success whether or not the new
text took, so an unverified body edit and a lost one are indistinguishable.
Quote the corrected line in the settle comment so the record shows what was
checked.

##### When you cannot fix it: choosing a reason

**A finding you cannot fix stops the *cycling*, not the work.** What "the work"
means depends on why you cannot fix it, and the four cases part company here:

- *Another account's PR.* You cannot push anything, so the work is
  writing the findings down where the author will act on them: **all** of them,
  including the ones that would have been a one-line fix, in the review comment
  and in a reply describing what you would have changed. Then apply `unsettled`
  with an `unsettled: not our branch @ <sha>` comment. The author's next push
  re-opens it.
- *This account's PR, but the push is refused.* Same work — write every finding
  down, including the mechanical ones — but apply `unsettled: latched @ <sha>`
  instead. **Not `not our branch`**: that reason re-opens on any commit, and at
  `own` the account that pushes is the one running, so the PR would re-open on its
  own pushes and cycle forever.

  **Unless a finding also needs a judgement — then `needs a decision` wins**, per
  the precedence under [the terminal labels and their
  reasons](REVIEW.md#the-terminal-labels-and-their-reasons), with the latch named in
  the comment as context. Where no finding needs one, `latched` is right and
  `needs a decision` would be wrong: what is blocked is the mechanism, not
  anything a reviewer raised.

  The distinction matters because the two are answered by different people doing
  different things. A latch is resolved by whoever can unblock the push; a
  judgement call is resolved by answering the reviewer's question. Filing one as
  the other loses it.
- *A finding needing a human decision, on a branch you may push to.* First fix
  everything else that round raised and push it — those findings are real and
  abandoning them wastes the round that found them. *Then* apply `unsettled`
  with an `unsettled: needs a decision @ <sha>` comment.
- *A finding needing a human decision, on a branch you may **not** push to.*
  **`needs a decision` wins over `not our branch`.** Both are true of this PR and
  only one of them is load-bearing: `not our branch` clears itself on the author's
  next push, so a judgement call given that reason is silently discharged by an
  unrelated commit and the human is never asked. Write up every other finding as
  in the first case, then apply `unsettled` with
  `unsettled: needs a decision @ <sha>`.

**Between `not our branch` and `needs a decision`, the reason is chosen by what
unsticks the PR, not by who owns the branch.** Ask "would the author's next push
actually resolve this?" — if not, `not our branch` is wrong, because its
carve-out fires on a commit that fixed nothing.

**This is a tie-break between those two, not a general test.** (Precedence
across all four is stated under the table in [the terminal labels and their
reasons](REVIEW.md#the-terminal-labels-and-their-reasons).)
Read as a general rule it would rule out `ran out of rounds` for every ceiling-
or budget-stopped PR — a push does not resolve those either, it only resets the
count — leaving `needs a decision` as the only reason the document could ever
apply. That is not the intent. A PR stopped by the ceiling or the budget with
nothing needing a judgement takes `ran out of rounds`, and its carve-out firing
on a commit is correct: new code genuinely does deserve fresh rounds.

**Where a judgement call is open, that outranks the ceiling and the budget** —
in **both** modes, so this changes `build` too, and deliberately. Both tell you
to apply `ran out of rounds`, and that reason clears on any commit — so a PR
carrying a decision-needing finding that also hit the ceiling would be silently
discharged by the author's next unrelated push, and the human never asked.

The cost is that such a PR no longer re-opens on a commit: it waits for a human
even though the author may have fixed everything else. That is the trade — a
question quietly dissolved is worse than a PR that waits — but it is the largest
behavioural change in this document's recent history and it is not confined to
`review-only`. When
both apply, **`needs a decision` wins**; note the ceiling or the budget in the
comment as context rather than as the reason.

**State the cost, because it is real.** `needs a decision` has no commit
carve-out, so parking a PR under it parks *every* finding on that PR behind a
human, including the ones the author could have fixed unprompted. On another
account's branch, that is the whole PR — which at `review scope: all` could be
most of the queue. The trade is deliberate — a judgement call quietly dissolved
by an unrelated commit is worse than a PR that waits — but write the other
findings up in full first, so the human clearing the label finds everything
they need in one place rather than just the question.

Either way, record what is left in the log and the report, and move on. Do not
spend further rounds on it: a new round is cold to your reasoning but not to the
code, so it re-derives the same finding off the same unchanged lines.

**Always post the reason comment.** The label alone says a PR is stuck without
saying what unsticks it, and the difference is whether it clears itself on the
author's next commit or waits for a human who has not been told they are needed.
