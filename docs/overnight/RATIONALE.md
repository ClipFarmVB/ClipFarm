# Why the machinery is shaped this way

Background, not rules. Read it once if you want to understand what the cycle costs and why it is built as it is — no rule depends on having read it.

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

#### What a night costs

**What this costs, plainly — and it depends on who opened the PR.**

*Every PR this account owns* — this run's and earlier runs' alike — is cycled in
full. Clean on first look costs two reviews; one round of findings costs three —
cold, semi-cold on the fix, cold to settle; two rounds costs five, which is most
of the seven-round ceiling.

**That is the number to plan the night from, and it changed on 2026-08-25.**
Before the push rule keyed on the account, PRs from earlier nights cost one or
two rounds and stopped, because the run could review them but not push to them.
They now cost three to five like any other. At `review scope: own` the whole
queue is in that category, so **budget three to five rounds per PR, not one or
two** — a night planned on the old figure binds around its fourth PR while the
text promises it will clear the queue.

*Other accounts' PRs*, which only enter the queue at `review scope: all`, still
cost one or two reviews and then stop: reviewable, not pushable, so a Critical or
Medium ends in `unsettled: not our branch` after a single cold round and a clean
one settles after two. Their fix loop runs on the author's push, on a later
night.

**The budget will bind on the first night, and that is expected.** Twenty-odd
PRs are open, none carrying a marker, so a first pass over the queue alone costs
one to two reviews each — forty-odd at the upper end, since a clean PR takes two
(it needs two clean cold rounds to settle) — before this run's own PRs are
reviewed at all.

**That argument was decisive at a budget of 32 and is not at 40.** Forty-odd
against forty is a coin flip, not a clear miss — the top of the estimate *is*
the budget. So the conclusion this paragraph originally drew, that the budget
binds on the first night, no longer follows from the arithmetic it presents. It
binds only if the queue runs to the upper end of the range.

What has not changed is the shape, and that is the part worth keeping: **a first
pass over an unmarked queue is the same order of magnitude as the whole
budget**, so it can still consume the night. Read it as *may bind* and re-derive
against the queue actually in front of you — which is what the paragraph below
already tells you to do, and now applies to this one too.

**That arithmetic assumes `review scope: all`.** It was written when there was
no scope filter, and the filter roughly halves it: with scope `own`, a queue of
twenty-odd is closer to ten, and most of a first pass fits inside the budget.
(The budget is **40 in both modes**. What differs is where reviewing stops: at
35 in `build`, leaving the five-round reserve for step 3, and at 40 in
`review-only`, where there is no step 3 to reserve for. The reserve is a
stop-reviewing threshold, not a smaller budget — the log still counts against
40.) So the two conclusions below — that step 3 does not happen, and that the
6-PR cap is unreachable — stop following. **Re-derive both for the scope you
are actually running**, rather than reading the worst case as a standing fact.

What follows from that: **the first night clears part of the queue and reports
the rest**, and later nights are cheap, because a PR that reached
`review-settled` or `unsettled` is skipped until its head SHA changes. The
budget is a bound on one night's spend, not a promise to cover the backlog.

The **ceiling** now binds far more often than it used to. It applies to any PR
this account owns that keeps yielding findings — which at `review scope: own`
is every PR in the queue it may push to, not just the handful this run opened.
The old reasoning (*a PR from an earlier night stops after one or two rounds
regardless*) was a consequence of not being able to push to it, and no longer
holds.

**So on a backlogged night — at `review scope: all`, see above — step 3 does
not happen, and the 6-PR cap is not reachable.** Twenty-odd PRs at one to two
rounds is 20–40 reviews, and six new PRs at three each is another 18: 38–58
against a budget of 40. At 32 nothing in that range fitted; at 40 the very
bottom of it does, and nothing above. So this conclusion has weakened from
*cannot* to *only if the queue is at its smallest and every PR converges in
three* — still the way to bet, no longer arithmetic that closes the question. Priority order gates ticket
work behind a queue this document says the budget cannot finish, so ticket work
waits for a night that starts with the queue already marked. That is the
intended trade — the queue is the bottleneck, not ticket supply — but it should
be read as a consequence, not discovered at 4am.

**Reserve five reviews for step 3 anyway.** Stop reviewing at 35 rather than 40,
so a night that *does* clear the queue early can still open one ticket and cycle
it. Without a reserve, step 1 always consumes everything and step 3 is dead by
construction rather than by circumstance.

**In `review-only` mode the reserve does not apply** — reviewing stops at 40, not
35. The reserve exists to protect step 3, and there is no step 3 to protect; five
rounds withheld for a step that cannot run are five rounds that simply go unspent.
Note what this is and is not: the reserve argument does not raise a cap, and does
not license raising one — if the ceilings make this mode impractical, say so in
the report rather than widening them. (The caps themselves were raised once, by
the operator, after the run of 2026-08-30; that is the mechanism — an operator
decision on evidence, not a run widening its own bounds.)

**In `build` mode, within that reserve: do not open a new PR unless at least
three reviews remain.** Two is the clean case only, and a PR with a single round
of findings costs three, so a smaller reserve guarantees the PR it just opened
ends `unsettled: ran out of rounds` by construction. A PR this run opens must be
reviewable by this run — a draft nobody has looked at is exactly what the hard
rules forbid leaving behind. If the budget cannot cover a review, step 3 writes
the plan into the log instead of opening a PR.

**That reason stands on its own, and it used to stand on another one that is
gone.** Under the old push rule, no later run could touch a branch this run
created, so a PR this run did not review would never be reviewed by anything —
the reserve was the only thing standing between step 3 and an abandoned draft.
Since 2026-08-25 a later run of this account can review *and* fix it, so that
argument no longer holds.

Keep the reserve anyway: **an unreviewed draft is a harm at the moment it is
opened**, not only if nobody ever gets to it. The hard rules say so
independently of who can push. The point of writing this down is that the
obsolete justification is exactly how a rule gets dropped — whoever next
re-derives it will find the old reason false and may conclude the rule is too.

#### Why the machinery is shaped this way

The semi-cold round is not defended on cost — it is defended on what silence
can and cannot establish. It asks a narrow question a reviewer can actually
answer: does this commit close this finding? Re-sampling the whole diff does not
answer that question however many times it is repeated, which is why fixes are
closed by a round pointed at them and settling still needs a cold one.

**Two knobs, and they are not interchangeable.** The **ceiling** (seven rounds per
PR) exists for fairness: it stops one pathological PR eating a night that twenty
others are queued for. The **budget** (40 rounds per run) sets total depth
across the queue. If runs are finding real problems and you want more review,
raise the budget — raising the ceiling only buys more passes over whichever PR
is already the worst-behaved.

**Both were raised at once after the run of 2026-08-30, which is the move this
paragraph argues against — so here is why the ceiling half was not "more passes
over the worst-behaved PR".** The ceiling was never a judgement about how much
review is worth buying; it is a calibration of the cost model directly above:
*five* rounds for a PR with two rounds of findings and the cold round that
settles it, plus a spare. #438 spent five and needed the spare, because each fix
drew a finding one spelling further out — and it **converged**. It was not the
pathological PR the ceiling exists to contain; it was an ordinary PR that the
cost model had priced too low, and at six it would have been cut off one cycle
short of settling and labelled `unsettled: ran out of rounds` on arithmetic
alone — the exact failure the settling exception was added to prevent.

So the ceiling raise is a **correction to the estimate**, and the budget raise
is the depth increase this paragraph is about. They are still different knobs;
they simply both moved.

**The honest weakness is n=1.** One PR is thin evidence for a permanent
calibration, and the direction of the error — needing more rounds — is the
direction that costs fairness to the rest of the queue. If a later run finds
the seventh round is routinely the one that settles, that is confirmation; if
seven-round PRs are ones that were never converging, the raise was wrong and
the ceiling should go back rather than up again. Hitting the ceiling is cheap anyway: the findings
are still written into the log, the report and the PR's marker comments, and
only the *settling* is deferred to a human or a later run.

None of this proves a PR clean. A cold subagent is unanchored but still the
same model with the same priors, so a new round is a different pass, not an
independent one: it finds *different* things, not *all* things, and the returns
diminish across rounds without reaching zero. That is exactly why there is a
ceiling and not just a clean bar. This cuts how many rounds a human has to run
by hand; it does not answer when a PR is actually done.
