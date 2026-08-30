# Run start

Read **once, at the start of a run**: what mode and scope this run has, what it may push to, what capabilities to check, and how work is chosen. None of it changes mid-run.

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

## Mode

A run has a **mode**, set in [This run](#this-run) before it starts. There are two,
and what differs is *which steps run*.

Rules defined in terms of a step 3 that does not run take a consequential
adjustment in `review-only` mode. **Each is amended where it lives, not here** —
this paragraph is an index, and if it disagrees with the rule it names, the rule
wins. Deliberately no count: an index that promises a number goes stale the
moment an amendment is added, and this one already did.

Note first that one change in this diff is **not** mode-specific: a judgement
call now outranks the ceiling and the budget when choosing an `unsettled`
reason, in `build` as much as in `review-only`. It is described under
[choosing a reason](FIX.md#when-you-cannot-fix-it-choosing-a-reason) and listed here
only so it is not mistaken for
a `review-only` amendment.

What is amended *by the mode*: the stop rule in
[Choosing work](#choosing-work), which
decides whether a `review-only` run proceeds at all; the scope subsection of
[This run](#this-run); the step-3 reserve, and the arithmetic that sizes it;
what a spent budget does; step 3 itself and everything under [Working a
ticket](TICKETS.md#working-a-ticket); two report bullets that go empty, one that is added
(which PRs the scope filter excluded); the report's required first line; and
the report's title.

| mode | steps 1 and 2 — review, fix | step 3 — ticket work |
|---|---|---|
| `build` — the default | yes | yes |
| `review-only` | yes | **no** |

**`review-only` restates nothing about how step 1 works.** Not ordering, not the
ceiling, not the budget, not the marker scheme, not the settle bar. It is a switch
on step 3, not a second brief. If anything in this section reads like a
*rule* about step 1, that is a bug in this section: go to
[step 1](REVIEW.md#step-1--which-prs-need-a-round) and follow what is written there.

**One exception, and it is deliberate: the review-scope filter below is a real
addition to selection.** It is written into step 1's selection rule itself so that
the authoritative statement and the enforced one are the same sentence — a filter
described only here would be discarded by the paragraph you are reading.

That warning is not decoration. The first draft of this feature restated selection
as "every open PR carrying neither `review-settled` nor `unsettled`" and so
silently dropped the trailing **and that label's carve-out has not fired** — which
is the clause that lets an author's fix-in-response-to-a-review re-open the PR. A
loop shipped with that wording would have made its own feedback path invisible to
itself.

**Why it exists.** Ticket supply is not the constraint; the open PR queue is.
`build` mode reaches step 3 only once the queue is clear, and reserves budget
against that possibility even on nights when it never arrives. `review-only`
spends the whole night on the queue.

**With one qualification that belongs here rather than in a budget note: at the
default `review scope: own`, "the queue" means this account's PRs** — a
fraction of the open set, and on this repo usually well under half. So the mode
does not, by default, address the backlog the paragraph above describes; it
addresses this account's share of it. That is a deliberate call (reviewing
other people's work unattended is a social change, not a technical one) and it
is defensible, but it means `review-only` at `own` is a narrower instrument
than "the queue is the bottleneck" implies. Run `review scope: all` when the
whole queue is what you actually want cleared.

### What it may push to, and what follows from that

The hard rule is [only push to PRs this account opened](RULES.md#hard-rules). At
`review scope: own` every in-scope PR is by definition this account's, so a
`review-only` run **can** push — to fix findings on work earlier runs opened.
That is the point of the mode: review the queue *and* clear it.

At `review scope: all` the queue also contains other accounts' PRs, and those it
still may not touch. So what it cannot push to is *other people's* work — and
since 2026-08-29 that is the whole of it; see
[The push test](RULES.md#the-push-test), which no longer reads commit authorship.

Two things follow, and both matter more than they look.

- **Two concurrent runs could now collide, and nothing structural prevents it.**
  This used to be answered by construction: `review-only` created no branches, so
  it never pushed, so two modes could not push to the same branch. That
  construction is gone — both modes now push to this account's branches.

  What is left is not a guarantee. `mode` is a single value in
  [This run](#this-run), so the brief cannot *express* two runs at once; that is
  an absence of expression, not an enforcement mechanism, and nothing stops two
  loops being started by hand. If that ever happens the failure is concrete: one
  run pushes a fix to a branch the other is mid-cycle on, the head moves under
  its rounds, and every round it has open is voided by the SHA test.

  The counters have the same exposure and always did — the ceiling and the budget
  are windowed by time and by the `reopened:` marker, with no notion of run
  identity, so a concurrent run's markers are counted as this one's.

  **So: do not start a second run while one is live.** That is scheduling
  discipline, stated plainly, because there is no longer a construction to hide
  behind. If concurrency is ever wanted, runs need an identity — in the markers
  and in the counters — and that is the work, not a wording change here.
- **Step 2 runs in full at `own`.** That is the whole of an `own` queue since the
  push test stopped reading commit authorship, so `review-only` fixes findings and
  re-reviews exactly as `build` does, and the difference between the modes is step
  3, not step 2. At
  `review scope: all` the other accounts' PRs are the ones step 2 cannot fix:
  describe the fix in a comment, apply `unsettled`, and post the reason that
  fits — `not our branch @ <sha>` when the fix is straightforward but
  unpushable, `needs a decision @ <sha>` when the finding needs a judgement
  nobody unattended should make. Those two are what *step 2* produces, and they
  are **not** the whole reason set: a PR stopped by the ceiling or the budget
  still takes `ran out of rounds @ <sha>`, in this mode as in `build`. That
  sentence is a pointer, not a restatement — it exists because a reader meeting
  two reasons here would otherwise take them as exhaustive. The reasons
  themselves are defined under [the terminal labels and their
  reasons](REVIEW.md#the-terminal-labels-and-their-reasons); if this
  bullet ever disagrees with them, they win. The second is not optional
  tidiness. Only `needs a decision` routes a PR to a human; giving a judgement
  call the `not our branch` reason means the next unrelated push clears it and
  the question is never asked.

**At `review scope: all`, a `review-only` run can still close findings on
another account's PR without ever pushing a fix itself.** (At `own` it simply
pushes the fix, like any other run.) The settle bar wants a semi-cold check,
and a semi-cold round checks a fix *whoever pushed it*: on another account's
branch, that is the author responding to the review, and [the brief requires
that case to work](REVIEW.md#cold-and-semi-cold-rounds) precisely so an `unsettled: not
our branch` PR is not stranded. So `unsettled: not our branch` is a **waypoint,
not a terminus** — the author's next push re-opens the PR, and the next run's
semi-cold round is what closes the finding.

What a single night in this mode delivers is **findings written where the author
will act on them**, plus `review-settled` on PRs clean across two cold rounds whose checks passed.
What the mode delivers *over several nights* is the full cycle: review, fix,
re-review, settle.

**At `review scope: own` the mode runs its own cycle end to end.** Every
in-scope PR is this account's, and every one is one it may push to — see
[The push test](RULES.md#the-push-test) — so it reviews, fixes what needs no
judgement, and re-reviews, without waiting on anyone. That is the mode working as
intended, and it is what the push rule changes of 2026-08-25 and 2026-08-29
bought.

*This paragraph previously said the opposite.* Under the old rule — push only to
branches **this run** created — a `review-only` night created no branches and so
could push to none of them: it parked the whole queue at `not our branch` and
the next night skipped everything. That was measured, not predicted: seven of
eight PRs in one working set, all this account's own earlier work. Keying the
rule on the account rather than the run is what removed it.

**At `review scope: all` the older caveat still holds** for the part of the
queue this account does not own: those PRs can be reviewed but not fixed, so
they depend on their authors pushing, and the multi-night loop is the mechanism
that closes them.

### Scope: whose PRs get reviewed

**The value to write is `own` in both modes unless a human decides otherwise:
only PRs whose author is the account the run posts as. Reviewing other people's
PRs is off, and turning it on is a human's call** — posting unattended reviews on
a teammate's work overnight is a social change, not a technical one, and it has
not been agreed.

"Default" here means the value an operator should normally write, **not** a value
to assume when none is written. An absent line is an unfinished edit, not a
choice; see the block below.

Set it in [This run](#this-run), alongside the mode:

```
review scope: own      # or: all
```

**This binds `build` too, deliberately.** The instruction not to review other
people's work unattended is not scoped to one mode, so neither is the default.

**Scope sits in front of everything, including the carve-outs.** A PR the filter
excludes is invisible to every downstream rule, so its labels stop meaning what
they say: a teammate's PR carrying `unsettled: needs a decision` promises that
removing the label brings it back in, and under scope `own` that promise does not
hold on any night. Same for `not our branch` and `ran out of rounds`, whose
carve-outs fire on a commit that the loop will never look at.

That is not an argument for scoping to `all` — it is a thing to **say out loud**.
Un-parking someone else's PR takes a human setting `review scope: all` for that
run, and the report's exclusion list is what tells them the PR is waiting on
exactly that.

**An out-of-scope PR is not an unreviewed one** — but be honest about what that
means on the PR itself. Step 1's filter puts such a PR *outside the queue*
rather than in it and skipped, so no round is owed and no label is missing. It
does, however, receive **nothing at all**: no round, no marker, no review, no
label, no comment. On the PR, it is indistinguishable from one this run never saw
— because that is what it is.

That is the one place this document's own principle — state should be
recoverable from the PR — does not hold, and it cannot: writing a marker to say
"deliberately not reviewed" would put a non-round in the stream every rule reads.
So the **report is the only record**, which is why naming the excluded PRs there
is a requirement and not a courtesy. A reader who wants to know why a PR went
untouched has exactly one place to look.

**Scope and pushability are the same test**, and both read `.user.login` on the
PR:

| question | test |
|---|---|
| may this run *review* the PR? | is `.user.login` this account, or is scope `all`? |
| may this run *push* to it? | is `.user.login` this account? |

So at `review scope: own` everything in the queue is pushable. At `all`, other
accounts' PRs are reviewable but not pushable at all.

*These have been three different rules.* Until 2026-08-25 the push test asked
which run created the branch, so a PR this account opened on an earlier night was
in scope to review and out of bounds to push to — the common case, and what
stalled the loop. Until 2026-08-29 it also refused any branch carrying a commit
authored by another login, which fired on ten of eleven PRs in one measured
queue and was a false positive every time; see
[The push test](RULES.md#the-push-test) for why authorship cannot answer the
question it was being asked.

### When a `review-only` run is done

It ends when either is true:

- **no in-scope PR needs a round**, by the test in
  [step 1](REVIEW.md#step-1--which-prs-need-a-round) — not a restatement of it, *that*
  test, carve-outs included; or
- the round budget is spent.

**Do not paraphrase the first condition.** The obvious phrasing — "every
in-scope PR carries `review-settled` or `unsettled`" — drops the trailing *and
that label's carve-out has not fired*, which is the clause that lets an author's
push re-open an `unsettled: not our branch` PR. A `review-only` run is the mode
most likely to be running while authors are asleep and then awake; a run that
reads a re-opened PR as terminal halts with budget in hand and the fix
unreviewed, which is the "waypoint, not a terminus" promise broken exactly where
it matters. This is the failure [Mode](#mode) warns about, so it gets no
exception here.

On the second, see [the budget rule](RULES.md#the-run-budget) — that is where the
amendment lives, and it is the statement that governs.

---

## This run

**No staleness date here on purpose.** A date only tested freshness by proxy: it
went stale whenever an operator changed the labels, the mode or the environment
without editing it, and the run then stopped and asked about a block that was
in fact current. That is a false stop on the common path, which costs a whole
night.

**What replaces it: say what you read.** Echo the mode, the review scope and any
narrowing verbatim into the log on the first iteration and into the report, as
the values you are actually operating under. A block nobody updated then shows
up as a wrong scope in the report — visible, and after one night rather than
never — instead of as a date nobody maintained. Do **not** infer that a block
is stale and act on the inference; the operator owns this section, and a run
that second-guesses it is guessing.

The one case that still stops the run is an unreadable block, not an old one:
if the block is missing, or either value is one you do not recognise, stop and
ask — see below.

```
mode: build            # or: review-only
review scope: own      # or: all
```

**If this block is missing, or either value is one you do not recognise, stop
and ask** — do not assume. The defaults named here are `build` and `own`, and
they are what an operator who wrote the block intended; an operator who deleted
it, or typed something else, has not told you anything. This section is the one
a human rewrites each run, so a missing block is as likely to mean "half-edited"
as "left at defaults", and the two differ by whether the run reviews other
people's work.

See [Mode](#mode). The mode decides whether ticket work happens at all; *which*
tickets is governed by [Choosing work](#choosing-work) under Standing policy,
not by the block below.

### Scope for tonight

Selection is governed by [Choosing work](#choosing-work) under Standing policy,
and does not change run to run. Use this section only to *narrow* it — a subset
of tickets, an area to avoid — never to restate or relax the gate. If there is
nothing to narrow, say so and leave it at that.

**In `review-only` mode this whole subsection does not apply.** A run with no
ticket queue is not a finished run, it is a run that was never going to do
ticket work; it stops on the condition in [Mode](#mode) instead.

The stop rule that would otherwise fire — *if nothing carries the label, stop the
loop* — is amended where it lives, in [Choosing work](#choosing-work), not
disabled from here. Turning off a Standing policy rule from a section the next
operator rewrites is the thing this section is forbidden from doing.

### Environment notes for this run

- Render is **suspended** at $0/month. Starting it costs money.
- `claude-review.yml` is **disabled**, so nothing reviews PRs automatically.

---

## Standing policy

### Choosing work

Work issues carrying the **`overnight-ok`** label:

```
gh issue list --state open --label overnight-ok --json number,title,labels
```

That label means a human has judged the ticket safe to implement unattended —
well-specified, no design or legal decision, no production data, no credentials.
It is the selection gate. **Do not take an issue that does not carry it**,
however appealing it looks; if you think one deserves it, argue for it in the
report instead of taking it.

Work highest priority first (`P0` > `P1` > `P2` > unlabelled). One ticket per
iteration. If a ticket turns out to need a decision after all, say so in the
log, drop it, and move on — do not guess.

If nothing carries the label, or everything that does is done, **stop the loop**.

**In `review-only` mode this rule does not apply.** That mode never reaches step
3, so an empty ticket queue says nothing about whether there is work: a night
with no `overnight-ok` issue is a perfectly ordinary night for reviewing a
backlog of PRs. Stopping on it would end the run before it reviewed anything,
which is the opposite of the mode's purpose. `review-only` stops on the
condition in [Mode](#mode) instead.

These are rules, not scope, which is why they live here: an operator rewriting
"This run" for tonight would otherwise discard the safety gate along with last
night's ticket list.

### First: establish what you can actually do

Before relying on any capability, check it, and record the result in the log in
one block. The first run discovered three gaps separately, mid-work.

- **Projects v2** — `gh project item-list 1 --owner ClipFarmVB --format json`.
  Needs the `project` token scope, which is often absent. This does **not** gate
  any work, and it does **not** stop cards reaching the board — see below. It
  only affects *editing* the board: without it you cannot remove the report
  issue or change a field. Note that, and carry on.
- **`gh` itself** — `gh --version`. Every command here is written in `gh` and
  some environments have none of it. That is an expected case, not a blocker:
  see [What a non-`gh` tool must provide](REVIEW.md#what-a-non-gh-tool-must-provide),
  and name the tool you used in the report.
- **Docker** — `docker info`. If absent, the local stack and the eval harness
  cannot run at all.
- **`gh` against this repo** — `gh api repos/ClipFarmVB/ClipFarm --jq .full_name`.
  Every command in this document is written for `gh`, and each was verified
  against this repo in the exact form given. A cloud runner may have no `gh`
  credentials at all — the first unattended run got 403 on every repo endpoint
  with GraphQL disabled, and did the whole night through MCP tools instead.
  **That works, and is not a reason to stop.** But say in the report which tool
  you actually used. The data requirements a non-`gh` tool must meet are written
  against that answer — they are in [what a non-`gh` tool must
  provide](REVIEW.md#what-a-non-gh-tool-must-provide), under
  "these commands are specifications", not in the bullet below this one.
- **Gate tool versions** — read the versions `ci.yml` installs and compare with
  what is installed here. `requirements-tooling.txt` is pinned, so there is a
  version to match; install that file rather than `pip install ruff mypy pytest`,
  and report the versions you actually ran either way.
- **A Postgres for the api suite** — `pg_isready -h localhost -p 5432`, which is
  where `api/tests/_pg.py` probes. Without one the api suite still passes:
  **8 skipped, exit 0**, the CF-184 advisory-lock and post-visibility tests
  silently absent. That is the one capability gap on this list which does not
  announce itself — everything else here fails loudly when missing, so a run
  that skips this probe reports a green gate it did not run. If there is no
  cluster, say so in the report and treat every api-suite result as eight tests
  short. If yours is elsewhere, set `LOCK_TEST_DATABASE_URL` to it; `_pg.py`
  takes a set value **unprobed**, so an unreachable one turns four of those
  eight skips into hard errors and the exit non-zero — the other four still
  skip. `build` runs meet this again in the gate list — see [Working a
  ticket](TICKETS.md#working-a-ticket), which carries the counts for all three
  states — but this list runs in `review-only` too, where the gate list is
  never reached.

State every gap in the report. A capability you assumed and did not have is the
most expensive kind of surprise in an unattended run.
