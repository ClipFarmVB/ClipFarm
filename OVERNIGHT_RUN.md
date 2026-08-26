# Unattended run brief

Instructions for an agent running `/loop` with nobody watching — overnight, or
any stretch where questions cannot be answered.

Start one with:

```
/loop Read OVERNIGHT_RUN.md and follow it exactly. Re-read .claude/overnight-log.md first each iteration so you do not repeat work.
```

The log stays gitignored on purpose: it is scratch memory for one run. The report
that has to survive is posted as an issue — see [Reporting](#reporting).

> **Update "This run" before starting.** Everything under
> [Standing policy](#standing-policy) holds every time. The section immediately
> below does not, and an agent given a stale scope will work confidently on the
> wrong things. This repository has been bitten by exactly that: CF-192's
> worst-case reasoning was invalidated by CF-224 without the text changing, and
> CF-224 read as "fixed" while production was still failing.
>
> **Nothing that is discardable may carry a rule.** "This run" holds scope —
> what tonight's environment looks like, and any narrowing of it — and never
> behaviour. If a run learns something that should change how future runs act,
> that belongs under [Standing policy](#standing-policy), even when the lesson
> came from tonight's scope. A fix written into a section the next reader is
> told to replace has not been made: it will be discarded unread, while whoever
> wrote it believes it landed. That happened twice in the first real run, and
> one of the two was reported as done.

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
[Priority order](#priority-order) and listed here only so it is not mistaken for
a `review-only` amendment.

What is amended *by the mode*: the stop rule in
[Choosing work](#choosing-work), which
decides whether a `review-only` run proceeds at all; the scope subsection of
[This run](#this-run); the step-3 reserve, and the arithmetic that sizes it;
what a spent budget does; step 3 itself and everything under [Working a
ticket](#working-a-ticket); two report bullets that go empty, one that is added
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
[Priority order](#priority-order) and follow what is written there.

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

The hard rule is [only push to PRs this account opened](#hard-rules). At
`review scope: own` every in-scope PR is by definition this account's, so a
`review-only` run **can** push — to fix findings on work earlier runs opened.
That is the point of the mode: review the queue *and* clear it.

At `review scope: all` the queue also contains other accounts' PRs, and those it
still may not touch. So what it cannot push to is *other people's* work — and,
at either scope, any branch a collaborator has pushed to; see
[The push test](#the-push-test).

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
- **Step 2 runs in full at `own` on the PRs it may push to.** That is most of an
  `own` queue — everything except branches a collaborator has pushed to — so
  `review-only` fixes findings and re-reviews exactly as `build` does, and the
  difference between the modes is step 3, not step 2. At
  `review scope: all` the other accounts' PRs are the ones step 2 cannot fix:
  describe the fix in a comment, apply `unsettled`, and post the reason that
  fits — `not our branch @ <sha>` when the fix is straightforward but
  unpushable, `needs a decision @ <sha>` when the finding needs a judgement
  nobody unattended should make. Those two are what *step 2* produces, and they
  are **not** the whole reason set: a PR stopped by the ceiling or the budget
  still takes `ran out of rounds @ <sha>`, in this mode as in `build`. That
  sentence is a pointer, not a restatement — it exists because a reader meeting
  two reasons here would otherwise take them as exhaustive. The reasons
  themselves are defined under [Priority order](#priority-order); if this
  bullet ever disagrees with them, they win. The second is not optional
  tidiness. Only `needs a decision` routes a PR to a human; giving a judgement
  call the `not our branch` reason means the next unrelated push clears it and
  the question is never asked.

**At `review scope: all`, a `review-only` run can still close findings on
another account's PR without ever pushing a fix itself.** (At `own` it simply
pushes the fix, like any other run.) The settle bar wants a semi-cold check,
and a semi-cold round checks a fix *whoever pushed it*: on another account's
branch, that is the author responding to the review, and [the brief requires
that case to work](#priority-order) precisely so an `unsettled: not our branch`
PR is not stranded. So `unsettled: not our branch` is a **waypoint, not a
terminus** — the author's next push re-opens the PR, and the next run's
semi-cold round is what closes the finding.

What a single night in this mode delivers is **findings written where the author
will act on them**, plus `review-settled` on PRs clean across two cold rounds.
What the mode delivers *over several nights* is the full cycle: review, fix,
re-review, settle.

**At `review scope: own` the mode runs its own cycle end to end.** Every
in-scope PR is this account's, and every one it may push to — all but the
collaborator-latched ones, see [The push test](#the-push-test) — it reviews,
fixes what needs no judgement, and re-reviews, without waiting on anyone. That
is the mode working as intended, and it is what the push rule change on
2026-08-25 bought.

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

**Scope and pushability share their first test**, and both read `.user.login`
on the PR — pushing then adds a second condition:

| question | test |
|---|---|
| may this run *review* the PR? | is `.user.login` this account, or is scope `all`? |
| may this run *push* to it? | is `.user.login` this account, **and** no commit on the branch carries another login? |

So at `review scope: own` everything in the queue is pushable **unless a
collaborator has pushed to it** — see the branch check in
[Hard rules](#hard-rules). At `all`, other accounts' PRs are reviewable but not
pushable at all.

*Until 2026-08-25 these were different questions* — the push test asked which run
created the branch, so a PR this account opened on an earlier night was in scope
to review and out of bounds to push to. That was the common case and it is what
stalled the loop; see [Hard rules](#hard-rules).

### When a `review-only` run is done

It ends when either is true:

- **no in-scope PR needs a round**, by the test in
  [Priority order](#priority-order) — not a restatement of it, *that* test,
  carve-outs included; or
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

On the second, see [the budget rule](#priority-order) — that is where the
amendment lives, and it is the statement that governs.

---

## This run

**Last updated: 2026-08-25.** If that date is not recent, stop and ask before
running.

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
- **Docker** — `docker info`. If absent, the local stack and the eval harness
  cannot run at all.
- **`gh` against this repo** — `gh api repos/ClipFarmVB/ClipFarm --jq .full_name`.
  Every command in this document is written for `gh`, and each was verified
  against this repo in the exact form given. A cloud runner may have no `gh`
  credentials at all — the first unattended run got 403 on every repo endpoint
  with GraphQL disabled, and did the whole night through MCP tools instead.
  **That works, and is not a reason to stop.** But say in the report which tool
  you actually used. The data requirements a non-`gh` tool must meet are written
  against that answer — they are in [Priority order](#priority-order), under
  "these commands are specifications", not in the bullet below this one.
- **Gate tool versions** — read the versions `ci.yml` installs and compare with
  what is installed here. See the gate step below.

State every gap in the report. A capability you assumed and did not have is the
most expensive kind of surprise in an unattended run.

### Hard rules

- **Never** push to `main`, merge a PR, or force-push anything.
- **Only push to the branch of a PR opened by the account this run posts as, and
  only if nobody else has pushed to it.** Any run's PR, not just this one's. If a
  fix belongs on a PR **another account** opened, or on a branch a collaborator
  has touched, describe it in a review comment instead and never push. Both halves
  are spelled out under [The push test](#the-push-test) below.
- **Never** deploy, unsuspend a hosting service, or touch production
  infrastructure.
- **Never** run the local stack against a `DATABASE_URL` pointing at Supabase.
  Confirm `.env.docker` names the local `db` container first.
- **Never** read, echo, or commit `.env.docker` or any credential.
- Every PR opens as a **draft**. It is work nobody has vetted yet, and draft is
  the honest signal for that. A draft reviews and takes pushes exactly like any
  other PR. You never merge, never deploy.
- **You are never the reviewer.** Every PR still gets reviewed — by a subagent
  spawned per step 1, whether or not this session wrote the diff.
- **Maximum 5 new PRs** and **6 new cards** per run.
- **No attribution stamps that you write.** Do not add "Generated with Claude
  Code", a `Co-Authored-By` trailer, a session link, or any similar footer to
  commits, PR bodies, reviews, comments, or issues. Local settings suppress
  these but a sandbox does not inherit them, so this is on you.
  **The two named above are never exempt**: both are emitted client-side and
  both are suppressible, so an agent finding one on its own output has a setting
  to fix, not an exception to claim.

  The exemption is for footer text you cannot prevent — identify it by
  reproducing it, not by reasoning about where it came from: post once, read the
  result back, and if text you did not write is present, quote it verbatim in
  the report and carry on. Never hand-edit a comment to strip it. "It must be
  server-side" is not a test the run can perform, and it is exactly the reasoning
  that would let a stray `Co-Authored-By` through.
- If a command fails because of usage limits, **stop the loop** — do not retry.
- If nothing in scope is actionable, **stop the loop**. A run that reviews two
  PRs and opens nothing is a fine outcome.

### The push test

Two conditions, both required, and the second has to be re-run each time.

**1 — This account opened the PR.** Compare `gh api user --jq ".login"` against
the PR's `.user.login`. That is the same test the `review scope` filter runs, so
at `review scope: own` it is true of everything in the queue.

*Phrased as PR authorship rather than branch ownership because authorship is what
the test reads.* GitHub does not expose "who owns the branch", and a rule written
in terms its test cannot evaluate is a rule that drifts from its enforcement.

**2 — Nobody else has pushed to the branch.** The two can diverge, and the
divergence runs in the risky direction: a collaborator may push commits to a
branch whose PR this account opened. The author test passes, so without this the
run would treat the PR as its own and land fixes on someone else's in-flight
work.

```
ME=$(gh api user --jq ".login")
COMMITS=$(gh api --paginate repos/ClipFarmVB/ClipFarm/pulls/<n>/commits --jq '.[].author.login // "UNKNOWN"')
[ -n "$COMMITS" ] || { echo "cannot read commits — do not push"; }
printf '%s\n' "$COMMITS" | sort -u | grep -vx "$ME"
```

**Any output means do not push.** Empty output means every commit is this
account's — *but only if the read succeeded*, which is why `$COMMITS` is checked
separately. A PR always has at least one commit, so an empty raw list means the
call failed, not that the branch is clean. Without that check the guard fails
**open** on a network error, a rate limit, a wrong PR number or a token missing a
scope: `gh api` writes to stderr, stdout is empty, and empty reads as "push".

**Run it immediately before each push, not once when you pick the PR up.** A run
holds a PR across several rounds, and the failure this guards against is a
collaborator pushing *while that is happening* — which is the same reasoning that
makes the head SHA get re-read after every round.

Three things about the command:

- **`.author.login` is correct *here*, and it is the one place in this document
  that is so.** [The author field rule](#priority-order) says to use
  `.user.login`, in bold, and it is right — about `pulls/<n>`. This is
  `pulls/<n>/commits`, a different payload: its objects carry `author` and
  `committer` and **no `user` at all** (verified on #311). "Correcting" this to
  `.user.login` would yield `UNKNOWN` for every commit, fire the guard on every
  PR, and make the whole queue unpushable — reinstating the stall CF-270 removed,
  silently, behind a guard that looks like it is working.
- **Not `gh pr view --json commits`.** It caps at 100 with no paging, and its
  author field is the *commit* author, which a rebase or a co-authored commit
  misattributes — so it fires on the account's own rebased branches.
- **`// "UNKNOWN"` fails closed.** `.author.login` is `null` for a commit whose
  email is linked to no account; a bare `.author.login` lets those read as "no
  other login", and the guard never fires. Mapping them to `UNKNOWN` makes an
  unverifiable branch count as someone else's. Measured 2026-08-25 on #190, #191,
  #214, #243 and #288: 0 nulls across 36 commits, so this should be rare — if it
  stops being rare, report that rather than working around it.

**The bound this command does not clear.** `pulls/<n>/commits` returns at most
250 commits however you page it (`per_page` itself caps at 100). Past 250 the
guard examines a prefix, and a collaborator commit beyond it reads as absent —
a fail-open in the guard whose other decisions all fail closed. No PR here is
near that, so this is a stated bound rather than a live problem: **if you meet a
PR with more than 250 commits, do not trust the guard — treat the PR as another
account's and say so in the report.**

*This was "branches this run created" until 2026-08-25.* That rule was
conditioned on a sign-off it never received, and the cost was measured: of the
eight PRs in one night's working set, seven ended `unsettled: not our branch`
and **all seven were this account's own work from earlier runs**. The loop could
review everything it had built and fix none of it. The sign-off is now given:
earlier runs of this account are this account.

**If the harness still refuses the push, that is a separate gate and this rule
does not override it.** Report the refusal rather than working around it.

### Log before you finish each iteration

Append a dated section to `.claude/overnight-log.md`: what you did, what you
decided and why, and anything needing a human call. **Read it at the start of
every iteration.** Context may be compacted between iterations; the log is the
only thing that survives.

**One thing goes in at the start of the run, not the end of an iteration:** the
run's own start time, in this shape, on a line of its own:

```
run start: 2026-08-25T04:12:09Z
```

**Find it by matching the line, never by reading the log's first line.**
[Reporting](#reporting) puts the run summary at the top of this same file, so
whichever is written last owns the first line:

```
SINCE=$(grep '^run start: ' .claude/overnight-log.md | tail -1 | cut -d' ' -f3)
[ -n "$SINCE" ] || { echo "no run start in log"; exit 1; }
```

`tail -1`, not `grep -m1`: nothing truncates this log, so the first match is the
*oldest* run's start and the newest is what you want. And guard the empty case —
an unset `SINCE` makes `.created_at > ""` true for every comment, which turns
every per-run bound into an all-time one silently. The guard **exits**; a guard
that only prints lets the failure it detected proceed anyway. Both failures point the same
way as the `$(date …)` trap below: they widen the window rather than narrowing
it, so nothing errors and the ceiling arrives early.

A resolved timestamp, UTC and `Z`-suffixed — produce it with
`date -u +%Y-%m-%dT%H:%M:%SZ` and write the **result**. Writing the command
itself into the log is not a near miss: everything downstream compares strings,
`$` sorts below every digit, so a literal `$(date …)` on that line makes every
comparison true and the per-run bounds silently become all-time ones. Several
bounds are recovered by comparing against this line after a compaction — see
[Priority order](#priority-order). Write it before the first iteration does
anything.

### Priority order

Finish work already in flight before starting anything new.

**Steps 1 and 2 are the two halves of one PR's cycle, not two sweeps over the
queue.** Read them as: pick a PR that needs a review, then carry *that* PR
through review and fix and re-review until it reaches a terminal state, then
pick the next one. Running step 1 across every open PR and only then starting
step 2 is the breadth-first pass ruled out below.

**1 — Review open PRs that need one.** One test, not two:

> **A PR needs a round unless it carries `review-settled` or `unsettled` and
> that label's carve-out has not fired.**

**And one filter in front of that test: the run's `review scope`.** With scope
`own`, a PR whose author is not this account is out of scope and gets no round —
it is not skipped-because-settled, it is not in the queue at all. With scope
`all`, every open PR is in the queue.

The value comes from the `review scope:` line in [This run](#this-run) —
**read it there, and if it is missing or holds a value you do not recognise,
stop and ask** rather than assuming `own`. This is the enforcement point, so a
run reaching it without having re-read that block would otherwise default
silently; see [Scope](#scope-whose-prs-get-reviewed).

**Out of scope means untouched: no round, no label, no comment.** The only place
such a PR appears is the report's exclusion list. That belongs here, at the
moment the filter is applied, rather than only in the section it links to — the
run of 2026-08-25 applied `unsettled: needs a decision` to 13 out-of-scope PRs
it had never reviewed, with the forbidding sentence sitting 250 lines away.

**The author field is `.user.login`, not `.author.login`.** On the REST `pulls`
endpoint this document uses everywhere, `.author` is `null` — verified on #291,
where `.author` returns `null` and `.user.login` returns the account. Only
`gh pr view --json author` populates `.author`, and that is the GraphQL path.
Getting this wrong is silent and fails toward *less* review: under the default
`own` scope, `null` matches no account, every PR drops out of the queue, and the
run reports a full queue as a deliberate scoping decision having reviewed
nothing. It is the same REST-versus-GraphQL trap as `created_at` against
`createdAt`.

```
gh api repos/ClipFarmVB/ClipFarm/pulls/<n> --jq ".user.login"
```

Note that scope and pushability now share a test rather than being unrelated
questions: both ask whether the PR's `.user.login` is this account. Pushing adds
one further condition — that no collaborator has pushed to the branch, see
[The push test](#the-push-test) — so a PR in scope at `own` is one this run may
push to *unless it is latched*. They were wholly different questions while the
push rule keyed on which run created the branch.

The obvious phrasing — "no marker at all, or commits since the last marker" —
leaves a hole. A PR sitting on its first `cold: clean` with no new commits has a
marker *and* no new commits, so neither clause selects it; yet that is precisely
the PR owing a second clean round before it may be labelled, and it would sit
there forever. **Unlabelled means unfinished.** What it needs next comes from
the routing table below.

Note also that "needs a round" is never decided from GitHub review objects. A
round does submit one — the two artifacts are described below — but the review
is not what *selection* reads. The review does open with the same marker line —
that is what tells a round's review from a human's — but every selection and
counting rule here is phrased against marker **comments**, and a rule phrased
against reviews would be counting an artifact that is deliberately outside the
budget and the ceiling.

**If you are not running `gh`, these commands are specifications.** Whatever
tool you use must give you **every numbered item below**, to the end of the
list. Deliberately not a count: this sentence has already been wrong once, when
an item was appended and the tally was not, and a run checking against the tally
stops one short of the requirement it most needs. Count the list, not this
sentence. The failure if any item is missing is silent rather than loud:

1. **Every** comment on a PR, not the first page. GitHub's own PR-comments
   shortcut caps at 100 and does not paginate; a capped read returns a stale
   marker and the machine routes on it confidently.
2. The comment **body verbatim**, so the first line and its SHA survive.
3. `created_at` per comment, for the run-scoped counts.
4. Review bodies and ids separately from comments — they are different objects
   and a tool that merges them breaks the marker/review split.
5. **Writes as two distinct objects**: a PR comment *and* a submitted review.
   A tool that posts everything as issue comments produces no review object, so
   step 2's review read finds nothing and exits, and the night's work never
   reaches the contribution graph. If your tool cannot submit a review, say so
   in the report — that is a real capability gap, not a detail.
6. **The PR's author login, and your own**, which the `review scope` filter
   compares. On REST the PR's is `.user.login`; `.author` is `null` there and
   populated only by `gh pr view --json`. Your own is `.login` from the
   authenticated-user endpoint:

   ```
   gh api user --jq ".login"
   ```

   A tool without `gh` needs both halves. **If it cannot tell you which account
   it is authenticated as, `own` scope is not computable — stop the loop and
   report the capability gap.** Do not widen to `all` to get unblocked: `all`
   means reviewing other people's PRs unattended, which
   [Scope](#scope-whose-prs-get-reviewed) says takes a human setting it for that
   run. A run that grants itself that permission because it could not read its
   own login has escalated its scope to work around a missing capability, and it
   would do so on the *common* path, since a run without `gh` is the expected
   case rather than the unusual one. [Hard rules](#hard-rules) already covers
   this: if nothing in scope is actionable, stop.

   Confirm you get a login and not a null before trusting the filter. A `null`
   under `own` excludes every PR silently, and the run reports a full queue as a
   deliberate scoping decision having reviewed nothing.

**Confirm your tool paginates before you trust a marker read**, and name the
tool in the report. A run that cannot establish point 1 should say so and treat
every marker read as unverified rather than assuming it saw the newest.

**Every round leaves one marker comment, and selection reads it.** Timestamps
alone cannot tell a PR stopped mid-cycle from one nobody has touched: a run can
die at any moment, including between a fix and the round that would settle it,
and at those points nothing changes on the PR. No new commit, so the
commits-since test above does not select it; no label yet, so neither skip rule
applies. It would be skipped forever — the abandoned-mid-cycle state these
labels exist to prevent.

So every round posts **exactly one comment** whose body **begins** with a
marker — the literal first characters, before any heading, bold or blank line.
`## cold: findings` does not match, and the house habit on this repo is to open
a review with a markdown heading, so this is the mistake to expect rather than
guard against loosely.

**Every marker ends with the head SHA it was formed against**, like
`cold: findings @ 2c1a865`. That SHA is what makes this scheme survive a
compaction: it records not just what happened but *what it happened to*, so a
later iteration can tell a verdict about the current code from one about code
that has since been replaced.

**Seven characters, from this command, everywhere.** `.head.sha` returns the
full forty, so it must be sliced — and sliced identically when writing a marker
and when testing one, or every comparison reports "differs", no SHA-matches row
can ever fire, and nothing settles:

```
gh api repos/ClipFarmVB/ClipFarm/pulls/<n> --jq ".head.sha[0:7]"
```

Routing reads the latest marker **and** compares its SHA with the PR's current
head:

- `cold: findings`, SHA **matches** head — the findings stand against this code.
  Next: step 2, fix them.
- `cold: findings`, SHA **differs** — a fix has already been pushed, and this is
  the case a marker alone cannot express. Next: a **semi-cold** round checking
  those findings against the new head. Without this row a compaction mid-fix
  routes the run back to step 2 forever, and the settle bar is never reachable.
- `cold: clean`, SHA matches — settle it **if the settle bar is met** (nothing
  Critical or Medium still open from *any* round), or one more cold round if the
  PR has never had a finding. A clean round is not by itself permission to
  label; see the settle bar.
- `cold: clean`, SHA differs — new code nothing has looked at. Next: a cold
  round, and see the re-open rule below.
- `semi-cold: closes` — a fix was checked, it closes the finding it claimed to,
  **and the round raised nothing new above a nit**. Next: a cold round.
- `semi-cold: does not close` — either the finding is still open, or the fix
  introduced a Critical or Medium of its own. Both are open findings and both
  route the same way. Next: step 2, fix it again. A cold round cannot rescue
  this one: the settle bar requires a semi-cold check to close a finding, and a
  cold round posted on top would make its own marker the latest and hide the
  open finding from this very rule.
- `semi-cold: does not close`, SHA **differs** — another fix has landed since.
  Next: another semi-cold round against the new head. Without this row a
  compaction after a second fix routes back to step 2 forever, exactly as the
  `cold: findings` case would.
- *no round marker at all* — the query below prints nothing. The PR has never
  had a round. Next: a cold round, the first-look case.

**Routing reads the latest `cold:` or `semi-cold:` marker — a round.** Two other
prefixes are written to the same stream and are deliberately *not* routed on:

- `reopened: <sha>` — records that a settled or unsettled PR came back with new
  code. It is not a round, consumes no budget, and decides nothing; it exists
  only to bound the counting windows below. Routing skips past it to the last
  real round, which is what makes the re-open rule and this table agree: an
  author who fixes an `unsettled: not our branch` PR leaves it on
  `cold: findings` with a differing SHA, which is the semi-cold row, not a cold
  one.
- `unsettled: <reason> @ <sha>` — the comment that records *why* the `unsettled`
  label went on. A later run reads it back to tell `needs a decision`, which
  wants a human, from `not our branch` and `ran out of rounds`, which do not.
  Nothing else records that distinction, so read it before deciding whether a
  labelled PR is eligible again:

  ```
  gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"^unsettled:\"; \"i\")) | .body | split(\"\n\")[0] | sub(\"\r$\"; \"\")" | tail -1
  ```

**Routing is a prefix test on the first line, folded to lowercase.** The
selection query returns that whole line, and a reviewer will naturally write
`cold: findings — 2 Critical, 1 Medium`; matching it against the four markers
for equality finds nothing. So compare the beginning of the line, not the whole
of it, and lowercase both sides — the query matches case-insensitively and the
router has to agree with it, or `Cold: findings` passes selection and then falls
off the routing table.

A trailing summary on that line is fine and worth encouraging — say so in the
brief when you spawn a round, because a reviewer reading "the body starts with
the literal marker" strictly is exactly the one who would otherwise have written
the useful summary. What is not fine is anything *before* the marker. Note also
that prefix matching separates `semi-cold: closes` from `semi-cold: does not
close` only because neither is a prefix of the other — preserve that if you ever
add a fifth marker.

**Use `ROUNDS` wherever something is counted or routed** — the six-round
ceiling, the 32-round budget, the latest-round lookup. A pattern that also
matched `reopened:` or `unsettled:` would charge those comments against the
ceiling and route on them, and neither is a round.

Every rule below that reads markers uses the same pattern. `gh`'s built-in
`--jq` takes a filter string only — it has no `--arg` — so the pattern is
interpolated by the shell and the filter's own quotes are escaped. Match it
**case-insensitively** (`; "i"`), so that a reviewer opening with `Cold:` does
not strand the PR:

```
ROUNDS='^(cold: (findings|clean)|semi-cold: (closes|does not close)) @ ?[0-9a-f]{7}'
```

**It matches the four routed forms and nothing else**, and it requires the SHA.
A broader pattern would match `cold: no findings @ abc1234` or a marker with no
SHA at all: selection would pick it up, the ceiling would be charged for it, and
routing would have nowhere to send it. Matching only what routes means a
malformed marker is invisible — which is the reading that fails safe, since
"this round did not happen" is exactly what a malformed marker tells you.
Re-post the marker correctly and note it in the report — a re-post is not a new
round and spends no budget, because nothing was ever counted for the malformed
one.

`reopened:`, `unsettled:` and `settled:` are matched by their own literal
prefixes where they are read, and are deliberately outside `ROUNDS` — they are
not rounds and must never be counted as any.

**Set `ROUNDS` in every shell that uses it**, including the round-counting query
hundreds of lines below, which runs in a later iteration and possibly after a
compaction. It is a pattern to re-declare, not state that persists. An unset
`ROUNDS` makes that filter `test("")`, which matches every comment on the PR and
returns exactly the count the marker scheme exists to avoid.

**Every machine-written comment goes through `gh pr comment` — rounds and
non-rounds alike.** That includes `reopened:` and `unsettled:`, which are
explicitly not rounds and so are not covered by the rounds rule below. A
`reopened:` marker posted as a review is invisible to the REST read, which
silently reverts the counting window to the PR's whole life — the exact failure
that marker exists to prevent.

**The marker goes through `gh pr comment`, never `gh pr review`.** A review body
does not appear in `gh pr view --json comments` at all — #191 carries three
comments and one review, and that query returns only the three — so a marker
submitted as a review strands the PR on whatever the previous comment said. This
rule has to travel to every subagent you spawn, and it is repeated in both
briefs below for that reason.

**Each round then also submits a review carrying its findings.** Two artifacts,
one round, and **the findings go in exactly one of them**:

```
gh pr comment <n> --body "cold: findings @ 2c1a865 — 2 Critical, 1 Medium"
gh pr review  <n> --comment --body "$(cat <<'EOF'
cold: findings @ 2c1a865 — 2 Critical, 1 Medium

## Critical
…
EOF
)"
```

Note the first line of the review body: it is the marker again, byte for byte.
A review that opens `## Critical` is invisible to the filter step 2 uses, so the
round that just ran cannot be read back — and it sits one hash away from a
maintainer's review, which is the confusion the marker line exists to prevent.

The comment is **only** the marker line — the prefix, the SHA, and at most a
one-line count. The review **repeats that marker line and then holds the tiered
findings in full**. Repeating one line is not duplication worth avoiding; the
findings appear once. Writing those into both would mean two copies of the same
text that can drift apart, and the graph credit comes from the review
*existing*, not from what the comment contains, so duplicating them buys
nothing.

**The review needs that line to be findable.** A round's review and a
maintainer's are both `state: "COMMENTED"`, and on this repo they come from the
same login, so nothing else distinguishes them — see step 2, which reads
findings back out.

The graph counts commits, issues opened, PRs opened and submitted reviews —
**not** conversation comments — so a loop that posts only comments does a night
of review work that never appears anywhere.

**A clean round still submits a review.** `gh pr review` rejects an empty body,
and the two rounds that award `review-settled` are exactly the ones with nothing
above a nit to report — so they would be the rounds that fail to post. Say what
was checked and that nothing above a nit was found, and list the nits. That is a
real review; it is the record that someone looked and found it clean.

**The two cannot collide, for exactly the reason the marker cannot be a review.**
Everything that reads state — `ROUNDS` matching, the latest-marker lookup, the
six-round ceiling, the 32-round budget — reads the comments endpoint, and a
review is not in it. So the review is invisible to every count, and adding it
changes no arithmetic anywhere in this document.

**"One comment per round" still means one *comment*.** The review is not a
comment and does not violate it. Do not collapse the two into one artifact in
either direction: a marker inside a review is unreadable, and findings with no
review are uncounted.

Read the latest marker, and route on it:

```
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"$ROUNDS\"; \"i\")) | .body | split(\"\n\")[0] | sub(\"\r$\"; \"\")" | tail -1
```

Empty output means the PR has never had a round.

**Do not use `gh pr view --json comments` for any of this.** It fetches
`comments(first: 100)` and never paginates, so on a PR past a hundred comments
it returns the *oldest* hundred — and this document expects a dozen rounds of
markers plus a fix reply each, on PRs that also carry human conversation. The
newest marker would simply fall outside the window: a late `cold: findings`
becomes invisible and a stale `cold: clean` settles a PR with open Criticals.
The REST endpoint above paginates properly.

**And note the shape of these commands: they stream and end in `tail`/`wc`,
rather than building an array and taking `last` or `length`.** With
`--paginate`, `--jq` runs *per page* — it prints one result per page, not one
for the whole set, and `--slurp` cannot be combined with `--jq` to fix that.
Array-and-`last` therefore yields a line per page, blanks included. Streaming
the matches and taking the tail is correct across any number of pages. REST
comments come back oldest-first, so `tail -1` is the newest.

Note also that REST spells the field `created_at`, not the `createdAt` that
`gh pr view --json` returns — a filter carried over from the GraphQL form
silently matches nothing.

The first-line extractions above end in `| sub("\r$"; "")`. Bodies written
through the web UI can carry CRLF, which leaves a trailing carriage return on
the marker line; harmless for the prefix test, but it corrupts the SHA when the
line is sliced or compared for equality, which routing now does.

**The terminal signal is the label, not the marker.** A PR carrying neither
`review-settled` nor `unsettled` is mid-cycle whatever its latest marker says,
and needs the round that marker names — including `cold: clean`, whose own
bullet above gives it a next action. The gap between a settling round and the
label it earns is a window like any other: no commit, no label, and a run can
die in it. Reading `cold: clean` as terminal is what strands such a PR, and it
strands the never-had-a-finding case twice over — once between its two clean
rounds, once after the second.

A `cold: clean` marker on an unlabelled PR therefore means: apply the label now
— settle bar permitting — unless the PR has never had a finding and carries only
one such marker, in which case it wants its second clean cold round first.

**Count only `cold: clean` markers whose SHA is the current head.** The rule is
about *this code* having been read clean twice, not about the PR's history: a
marker written against a head that has since been replaced says nothing about
what is there now. Windowing by the `reopened:` marker is not enough on its own,
because an unlabelled PR can take a push mid-cycle with no re-open involved, and
its stale `cold: clean` would still count. Matching the SHA covers both, and
needs no window at all — the `reopened:` marker carries the new head, so a
marker from before a re-open cannot match the current SHA anyway. Where you see
a `created_at > "$REOPENED"` clause below, that is the **finding** count, which
is not SHA-gated and does need the window.

It has its own pattern, and the same unset hazard as the others:

```
SHA=$(gh api repos/ClipFarmVB/ClipFarm/pulls/<n> --jq ".head.sha[0:7]")
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"^cold: clean @ $SHA\"; \"i\")) | .id" | wc -l
```

For a re-opened PR add the `select(.created_at > "$REOPENED")` clause, exactly
as the finding count below does — the same `REOPENED` value, read off the same
`reopened:` marker:

```
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"^reopened:\"; \"i\")) | .created_at" | tail -1
```

**"Never had a finding" spans the PR's whole life, not this run.** Every other
count in this section is scoped by `SINCE`; this one must not be, or a PR whose
findings were raised and closed last night reads as never-had-a-finding tonight
and settles a round early.

**Set `FINDINGS` in the shell you ask from**, for the reason the `ROUNDS`
warning gives: unset, it becomes `test("")`, matches every comment, returns
nonzero, and a PR that has never had a finding settles on one clean round
instead of two. That failure runs toward *less* review, which is the direction
this document guards hardest against everywhere else.

For a PR that has never been re-opened, ask over its whole life:

```
FINDINGS='^(cold: findings|semi-cold:)'
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"$FINDINGS\"; \"i\")) | .id" | wc -l
```

**On a re-opened PR, count only from the re-open.** New commits nothing has
confirmed anything about are the condition the two-clean rule exists for, and
the PR's older history is evidence about code that has since changed. That
window is **not** `SINCE` — the run start is the bound this section just ruled
out — but the PR's latest `reopened:` marker:

```
FINDINGS='^(cold: findings|semi-cold:)'
REOPENED=<the `created_at` of the PR's latest `reopened:` marker, or empty if it has none>
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.created_at > \"$REOPENED\") | select(.body | test(\"$FINDINGS\"; \"i\")) | .id" | wc -l
```

Zero from the first form means nothing has ever been found on this PR; zero from
the second means nothing has been found since it was re-opened. Either way it is
the case that needs two clean cold rounds — a semi-cold round only exists
because a finding did, so no marker means no finding.

**Skip PRs labelled `review-settled`** unless commits have landed since the
label was applied. That label is the record that a cold round cleared the bar
below — no Critical and no Medium finding, nits permitted. Without it, "already
reviewed" has to be inferred from timestamps, and a PR abandoned mid-cycle looks
identical to one reviewed clean.

**Skip PRs labelled `unsettled`.** It is the opposite record to
`review-settled`: Critical or Medium findings are open and this run cannot close
them.

**The label is bare `unsettled`. The reason goes in the comment, never in the
label name.** There are exactly two **review-state** labels — `review-settled`
and `unsettled` — alongside the ordinary ones the repo uses (`P1`, `api`,
`overnight-ok` and so on). `gh pr edit --add-label "unsettled: blocked"` fails against a
label that does not exist, leaving the PR unlabelled with open Criticals, which
is the one state this document forbids. So: apply `unsettled`, and post a
comment opening `unsettled: <reason> @ <sha>`. That comment is the only record
of which reason applies, and it is what a later run reads back:

- `unsettled: not our branch @ <sha>`
- `unsettled: needs a decision @ <sha>`
- `unsettled: ran out of rounds @ <sha>`

**Whatever the reason, the label needs a round from *this run* behind it.** The
label asserts that findings are open and this run cannot close them; with no
round, nothing looked, so there are none to be open — and `needs a decision` has
no commit carve-out, so it would park the PR on a human indefinitely on the
strength of a review that never happened. That is what the 2026-08-25 run did to
13 PRs.

Note the test is **a round from this run**, not a marker matching the current
head. Those differ, and the difference is not an edge case: the ceiling path
tells you to fix what you can and *push* before labelling, so by the time the
label goes on, the head has moved past the last round's marker. An author
pushing between a round and a `not our branch` label does the same. Requiring a
head-matching marker would make both cases impossible to label at all, while
[the rule against leaving open findings unlabelled](#priority-order) still
demands one.

Only one of the three needs a human to clear it:

- `ran out of rounds` — the ceiling or the budget stopped it. New commits
  re-open it, round count reset: a PR that has since been fixed must not look
  like one nobody touched.
- `not our branch` — the findings are fixable, but the branch belongs to
  **another account**, so this run may not push to it. **New commits re-open
  it**, round count reset. A commit is exactly what resolves this one: the author
  reading the review and pushing a fix is the intended path, and it must not need
  a human to also clear a label by hand.

  **At `own` this reason means a collaborator has pushed to the branch** — the
  guard in [The push test](#the-push-test) firing is the *correct* result, not a
  malfunction. At `all` it also covers PRs another account opened.

  **But do not use it for the collaborator case: use `needs a decision`.** The
  commits carve-out re-opens `not our branch` on every new commit, and at `own`
  the account that pushes is the one running — so a latched PR would re-open on
  each of its own pushes, burn a cold round re-deriving findings it may not fix,
  and re-park, forever. Worse, the carve-out's justification (*the author reading
  the review and pushing a fix is the intended path*) assumes the author is
  somebody else; here the author is this account, and it is the one actor that
  cannot act.

  `needs a decision` has no commit carve-out, so it parks once and waits — and it
  is the honest reason, because what such a PR actually needs is a human deciding
  whether to push over a collaborator's in-flight work.

  Note the latch is permanent by design: the guard reads the branch's whole
  history, so one commit from a collaborator makes the PR unpushable by this run
  from then on, even after the account pushes more. Scoping it to "since this
  account's last push" would let the run overwrite exactly the in-flight work
  being guarded.
- `needs a decision` — a finding requires a judgement nobody unattended should
  make. Only a human removing the label re-opens it. Commits do not, because the
  decision is not something a commit clears; the author pushing something
  unrelated would otherwise buy fresh rounds to re-derive the same finding off
  the same unchanged lines.

**Applying either terminal label posts a record comment**, and that is what
makes a human's removal detectable at all:

- `unsettled` → `unsettled: <reason> @ <sha>`
- `review-settled` → `settled: @ <sha>`

**A human clearing a label leaves no `reopened:` marker**, because nothing this
run did re-opened it. So when you pick up a PR carrying one of those record
comments but *not* its label — a maintainer removed it — **write the
`reopened: <sha>` marker yourself before the first round**, then let the routing
table pick the round, exactly as for a carve-out re-open. Do not force a cold
one: a maintainer who clears `unsettled: not our branch` *after* the author
pushed a fix leaves a PR whose last round is `cold: findings` at a stale SHA,
which wants a semi-cold check. Forcing cold there cannot close the finding, so
the settle bar stays unreachable and the PR burns to the ceiling.

Both labels need the marker. Without it for `review-settled`, a maintainer who
removes the label to ask for another look gets it silently re-applied with zero
rounds run: routing sees `cold: clean` at the current head, and the settle rule
says apply the label now.

**Guard against re-firing.** That rule describes a re-opened PR as well as a
human-cleared one, so bound it: act only if there is **no `reopened:` marker
newer than the record comment**. Without the guard, every compaction re-triggers
it, each new `reopened:` marker pushes `FROM` forward, and the round count
resets to zero on a PR that has been cycling all night. Otherwise the counting
windows silently revert to the PR's whole life, and the two-clean rule reads
clean markers from before the finding was ever raised.

**When a carve-out re-opens a PR, take the label off — and let the routing table
decide the round.** Do not force a cold one: what the PR needs depends on what
its last round said, and the table already tells the two cases apart by SHA. A
re-opened `review-settled` PR wants a cold round: its last round was
`cold: clean`, and the new commits are code nothing has read. A re-opened
`unsettled` PR — either reason that commits can re-open, `not our branch` or
`ran out of rounds` — wants whatever its last round marker says, which is
usually `cold: findings` at a stale SHA, and so a semi-cold check of the fix
that has since landed. So:

- **Post a `reopened: <sha>` marker first, then remove the label.** In that
  order. The label is the only record that the PR was ever settled or unsettled,
  so taking it off without leaving anything behind destroys the boundary the
  counting windows depend on — a later iteration then counts **finding** markers
  over the PR's whole life, so a PR whose findings were raised and closed long
  ago reads as still carrying them. (The clean count is safe here: it is gated
  on the current head SHA, so stale clean markers cannot count.) The marker is
  durable; the label was not. Left on, it also advertises
  `review-settled` to humans reading a PR with unreviewed commits.
- **Route on the last round marker, exactly as the table does** — the
  `reopened:` marker you just posted is not routed on. For a PR that was
  `review-settled` that means a cold round: its last round was `cold: clean` and
  the SHA now differs, so this is new code nothing has looked at. For a PR that
  was `unsettled: not our branch` it means a **semi-cold** round: its last round
  was `cold: findings`, and the author's push is a fix to check. Do not force
  cold here. Only a semi-cold round can close a finding for the settle bar, so
  forcing cold on an author-fixed PR makes its terminal state unreachable. That
  case is `review scope: all` only now, but the routing is the same either way:
  the marker says what the next round is, not who pushed.
- **Re-read the round count, bounded by the `reopened:` marker you just
  posted.** Removing the label resets nothing on its own — no count lives in a
  label. Removing it makes the PR *eligible*; the marker *bounds the count*.
  Skip that read and the count still runs from `SINCE`, which includes every
  round already spent on this PR earlier tonight, and the ceiling arrives early
  on the PR just promised a fresh one.

**Every carve-out is the SHA test, not a timestamp**, and it reads **the SHA
recorded when the label went on** — not the latest round marker. There is no
label event to read and no date to compare, so identity cannot be defeated by a
commit written before the review that pushed after it.

Which record carries that SHA depends on the label:

- `unsettled` — the `unsettled: <reason> @ <sha>` comment posted with it.
- `review-settled` — the `settled: @ <sha>` comment posted with it.

**Do not use the latest round marker for this.** On the ceiling path the run
fixes what it can and *pushes* before labelling, so by the time the label goes
on, the head has already moved past the last round's marker. Reading that marker
would report "commits since" on a PR nobody has touched since, re-opening it
with a fresh six-round ceiling on the very next look — turning the ceiling into
no ceiling at all. The label's own record is written at labelling time, after
that push, so it is the one that actually moves with the label.

Compare the PR's current head SHA with the SHA in its latest marker. Different
means code has landed that no round has seen:

```
ROUNDS='^(cold: (findings|clean)|semi-cold: (closes|does not close)) @ ?[0-9a-f]{7}'
SHA=$(gh api repos/ClipFarmVB/ClipFarm/pulls/<n> --jq ".head.sha[0:7]")
LATEST=$(gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"$ROUNDS\"; \"i\")) | .body | split(\"\n\")[0] | sub(\"\r$\"; \"\")" | tail -1)
MARKSHA=$(printf '%s' "$LATEST" | grep -oE '@ ?[0-9a-f]{7}' | head -1 | grep -oE '[0-9a-f]{7}')

echo "head:   $SHA"
echo "marker: ${LATEST:-<none>}"
if   [ -z "$LATEST" ];          then echo "first-look"
elif [ "$MARKSHA" = "$SHA" ];   then echo "same"
else                                 echo "differs"
fi
```

`grep -oE … | head -1`, not `sed`: a `sed` pattern of the form `.*@ \(…\)` is
greedy, so a summary mentioning a second `@` hands back the wrong seven
characters. `@ ?` also tolerates `@2c1a865` — write the space, but do not let a
missing one strand a PR that would then take a cold round every visit, burn to
the ceiling and re-open forever.

The block prints exactly one routing signal. An earlier version printed a
diagnostic *and* a verdict for the same PR, which reads as two contradictory
answers to a rule that takes one.

One block, because the three values are only meaningful together: the marker
line feeds the SHA extraction, and a snippet that leaves `$LATEST` unassigned
extracts nothing and reports `differs` for every PR.

**A malformed marker is invisible, and that is deliberate.** `ROUNDS` matches
the four routed forms and requires a SHA, so a comment reading
`cold: no findings @ abc1234`, or one with no SHA, or one behind a markdown
heading, matches nothing at all. It is not selected, not routed, and not counted
against the ceiling or the budget. The PR simply reads as though that round
never happened — which is the one reading that fails safe, and it is why a
re-post costs nothing: there is no phantom round to double-charge.

The cost is that a malformed marker is *silently* lost rather than flagged.
That is what the marker-landed check below is for: the round that wrote it is
the only thing positioned to notice, so it verifies at the time rather than
leaving a later iteration to infer it. If you find one after the fact, re-post
the marker correctly and note it in the report; do not guess what was meant, and
do not read it as clean.

**Compare SHAs, never dates.** A commit's `committer.date` is when it was
*written*, not when it was pushed: an author who committed on Tuesday and pushed
on Thursday produces a commit older than a Wednesday review, and a date test
concludes nothing has landed. That silently disables the
`unsettled: not our branch` carve-out — the case where another account's push is
the only thing that can continue the cycle. That case is `all`-scope only since
the push rule changed, but the carve-out still has to work when it arises, and a
date test breaks it silently. Identity has no such failure mode.

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
[Working a ticket](#working-a-ticket) already spawns one to cross-check plans —
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

**A "does not close" verdict leaves the finding open.** Fix it again and take
another semi-cold round, or, if you cannot, apply the `unsettled` label with a
comment naming the reason that fits — `not our branch` or `needs a decision` —
record it, and move on. It does not become closed by being argued with.

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
[Hard rules](#hard-rules) section wholesale, which would tell the reviewer you
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

Its brief: run `/code-review high` on that PR, post one marker comment, and
submit its findings as a review. Give it the head SHA you captured, and require
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

**2 — Address the review findings on the PR you are carrying**, if this account
opened it **and** no one else has pushed to the branch. The first half is the
same test the `review scope` filter runs — `gh api user --jq ".login"` against
the PR's `.user.login` — so at `review scope: own` it is true of every PR in the
queue. The second half is the collaborator check in
[Hard rules](#hard-rules), and it has to be run per PR: a PR this account opened
on an earlier night is pushable *until someone else pushes to it*, and then it
is not.

So step 2 applies to most of an `own` queue, not all of it by construction.

For a PR you may not push to — another account's, which only reaches the queue
at `review scope: all`; or one a collaborator has pushed to, which can arise at
either scope — describe the fix in a comment and apply `unsettled`. Post
`unsettled: not our branch @ <sha>` for the first case and
`unsettled: needs a decision @ <sha>` for the second, for the reason given with
those reasons. That is the cheap terminal state, not
a dead end: the author pushing a fix re-opens it on its own.

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

**When a cold round clears that bar, label the PR `review-settled` and post a
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

So: **fix nits before the settling round, or leave them.** Those are the
options. Do not push a nit fix after the label either — it re-opens the PR by
design, which buys another cold round, which can surface another nit, which
presents the same choice again. A PR can cycle indefinitely on nits alone,
spending budget every lap, and nits are what the settle bar deliberately
tolerates. Leaving one is the terminating move; file a card if it is worth more
than that.

**A finding you cannot fix stops the *cycling*, not the work.** What "the work"
means depends on why you cannot fix it, and the three cases part company here:

- *Another account's branch.* You cannot push anything, so the work is
  writing the findings down where the author will act on them: **all** of them,
  including the ones that would have been a one-line fix, in the review comment
  and in a reply describing what you would have changed. Then apply `unsettled`
  with an `unsettled: not our branch @ <sha>` comment. The author's next push
  re-opens it.
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

**This is a tie-break between those two, not a general test.** Read as a general
rule it would rule out `ran out of rounds` for every ceiling- or budget-stopped
PR — a push does not resolve those either, it only resets the count — leaving
`needs a decision` as the only reason the document could ever apply. That is not
the intent. A PR stopped by the ceiling or the budget with nothing needing a
judgement takes `ran out of rounds`, and its carve-out firing on a commit is
correct: new code genuinely does deserve fresh rounds.

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

**Ceiling: six rounds per PR per run, cold and semi-cold together**, so a
pathological PR cannot consume the whole night. Counting only cold rounds would
leave the semi-cold ones unbounded — every fix buys another check — and half of
a ceiling is not a ceiling. Six covers a PR with two rounds of findings and the
cold round that settles it — five by the cost model above, with one spare.
Hitting it is the same outcome: fix what you can, apply `unsettled` with an
`unsettled: ran out of rounds @ <sha>` comment, record, move on.

**One exception: a PR with nothing open may run the rounds settling needs, past
the ceiling.** If the sixth round leaves no Critical and no Medium outstanding,
settling still needs a fresh cold round, and refusing it labels a converged PR
`unsettled: ran out of rounds` on arithmetic alone. That happened on #291 in the
first real run: six rounds ending `semi-cold: closes — 4 of 4 Mediums closed,
nothing new above a nit`, nothing open, and the failure label applied anyway.

**"The rounds settling needs" is usually one, and is two for a PR that has never
had a finding** — that case wants two consecutive `cold: clean` markers, so
granting a single round would strand it exactly as the ceiling did. Grant what
the settle bar asks for, no more.

The exception terminates, which is why it is safe: **any finding ends it
immediately.** An extra round that raises a Critical or Medium stops the PR
there, and `unsettled: ran out of rounds` is then accurate rather than
arithmetic. Rounds that stay clean can only run until the bar is met, and then
the PR settles. There is no path that keeps granting rounds.

**These rounds are charged to the 32-round budget.** They are real reviews and
the counting query charges them automatically; unlike a `reopened:` marker or a
re-posted marker, nothing here is free. The exception lifts the *per-PR*
ceiling, never the run-wide budget.

**Take one PR all the way through before opening the next.** Review it, fix it,
check the fix, settle or label it — then move on. Do not run a pass over every
open PR and come back for a second lap.

The reason is that this loop gets interrupted: context is compacted between
iterations, and a usage limit stops the run outright, at no point of your
choosing. Finishing PRs one at a time means whenever that happens, everything
touched so far is in a terminal state — `review-settled`, `unsettled`, or
untouched — and the next run can tell those apart. A breadth-first pass that is
cut off leaves every PR half-cycled, which is precisely the "abandoned
mid-cycle looks identical to reviewed clean" condition these labels exist to
prevent. It also keeps the state you carry small: one PR's findings, not twenty.

The cost is real: if the run dies early, PRs at the back of the queue got
nothing at all. So the order matters. Take them: PRs this run opened, then any
carrying a priority label, highest first, then oldest first. Note that most open
PRs carry no labels at all, so in practice this is mostly "oldest first" — which
is the intent, since the oldest have waited longest. Do not order by the
`overnight-ok` label: that is the *issue* selection gate from
[Choosing work](#choosing-work) and no PR carries it.

**Run budget: 32 rounds per run**, cold and semi-cold together. *Rounds*, not
reviews: each round now submits a GitHub review as well as posting its marker,
so counting "reviews" would be ambiguous about which artifact is meant. The
budget counts rounds, and a round is one marker comment. Six rounds
across a queue this size would permit far more — a whole night of nothing but
reviewing, which together with "stop on usage limits" means step 3 never
happens. **When the budget is spent, stop reviewing and go to step 3** — but
step 3 may then only plan and file, **not open PRs**, because a PR opened with
no review budget left is a draft this run cannot review, which the hard rules
forbid. Say so in the report. A spent budget clears steps 1 and 2 for the rest of the run;
without that fall-through the brief would forbid reviewing and gate ticket work
behind reviews that can no longer happen, and specify nothing to do next.

**In `review-only` mode there is no step 3 to go to, so a spent budget ends the
run.** Do not read the fall-through above as permission to keep reviewing past the
budget because the destination is missing.

**"Ends the run" means it starts no new round — not that it stops mid-carry.**
Everything the paragraph below requires still happens: label the PR you are
holding, post its reason comment, and record it. A run that reads "ends" as
immediate leaves exactly the unlabelled-with-open-findings PR that paragraph
forbids.

If the budget runs out with findings open on a PR, it gets the same treatment as
the ceiling: `unsettled`, recorded, move on. Never leave a PR with open findings
carrying no label — unlabelled and unreviewed are indistinguishable to the next
run, which is the whole reason these labels exist.

**Log every round as you finish it** — `PR #<n> — <cold|semi-cold>, round
<k>/6, budget <used>/32` plus the tiers found. A round granted by the settling
exception is logged as `settling, budget <used>/32` instead of a `<k>/6` — it is
outside the ceiling, and writing `7/6` reads as a counting bug to the very
cross-check that is meant to catch one. Neither bound is enforceable
unless the count survives: context may be compacted mid-run, and counts you hold
in your head reset to zero when it is. Recover both from the log at the start of
every iteration, and cross-check **both** counts against the markers — the
per-PR round count, and the run-wide budget, which is the sum of this run's
markers across every PR it touched:

```
for n in $(gh pr list --state open --json number --jq '.[].number'); do
  gh api --paginate "repos/ClipFarmVB/ClipFarm/issues/$n/comments" --jq ".[] | select(.created_at > \"$SINCE\") | select(.body | test(\"$ROUNDS\"; \"i\")) | .id"
done | wc -l
```

The budget needs this as much as the ceiling does. Recovering it from the log
alone leans on the one source this same paragraph says a compaction can lose
entries from, and losing entries makes the budget read *low* — so the run keeps
reviewing past 32 and starves step 3, failing toward more reviewing rather than
less.

When log and markers disagree, **the markers win.** The log records
what a round intended; the markers record what the PR actually carries, and
every other rule here reads the PR. A log ahead of the markers means a round's
marker did not land, which the check above is there to catch at the time; a log
behind them means a compaction lost an entry. Neither is a reason to trust the
log over the thing the rules read. Count markers, not comments: comments also
carry your step 2 fix replies and anything a human wrote.

**Count only markers from this run.** Markers persist for the life of the PR;
the ceiling is six rounds *per run*, and an `unsettled: ran out of rounds` PR is
promised a reset when new commits land. A raw count undoes both — a PR that
spent six rounds last night would read as already at the ceiling before this run
touched it. So count markers newer than the run's start time, which the
[logging rule](#log-before-you-finish-each-iteration) puts on its own
`run start: ` line — found by matching that line, never by position.

**When a PR was re-opened mid-run, count from the `reopened:` marker instead —
but only if that marker falls inside this run.** There is no label event to read
here; re-opening writes that marker precisely so this bound survives the label
being removed. Three states carry a commits-since carve-out — `review-settled`,
and the `ran out of rounds` and `not our branch` reasons for `unsettled` — and
each re-opens the same way, so each gets the same bound. (`needs a decision` has
no carve-out and never needs it.) The bound you want is the *later* of the run
start and that marker: a `reopened:` marker from last night is older than the
run start, so counting from it sweeps in markers this run has already spent and
the ceiling arrives early on a PR just promised a reset.

`.created_at > "$SINCE"` is a lexicographic string compare against GitHub's
`2026-08-24T23:08:57Z`, so `SINCE` must be UTC with the `Z` suffix and nothing
else — which is what `date -u +%Y-%m-%dT%H:%M:%SZ` produces, and why the run
start is recorded in that form. An offset form like `2026-08-25T01:08:57+02:00`
sorts wrong against it and the count comes back low or zero — which reads as "no
rounds this run" and hands the PR a fresh six-round ceiling:

```
ROUNDS='^(cold: (findings|clean)|semi-cold: (closes|does not close)) @ ?[0-9a-f]{7}'
SINCE=$(grep '^run start: ' .claude/overnight-log.md | tail -1 | cut -d' ' -f3)
[ -n "$SINCE" ] || { echo "no run start in log"; exit 1; }
REOPENED=$(gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"^reopened:\"; \"i\")) | .created_at" | tail -1)
FROM=$(printf '%s\n%s\n' "$SINCE" "$REOPENED" | sort | tail -1)
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.created_at > \"$FROM\") | select(.body | test(\"$ROUNDS\"; \"i\")) | .id" | wc -l
```

`FROM` is the later of the two, which is what the rule above says and what
`$SINCE` alone does not give you: a PR re-opened earlier tonight would otherwise
be counted from the run start, sweeping in the rounds it already spent and
hitting the ceiling early — the failure this section exists to prevent. Sorting
`Z`-suffixed UTC lexicographically picks the later; an empty `REOPENED` sorts
first and leaves `SINCE`.

**What this costs, plainly — and it depends on who opened the PR.**

*Every PR this account owns* — this run's and earlier runs' alike — is cycled in
full. Clean on first look costs two reviews; one round of findings costs three —
cold, semi-cold on the fix, cold to settle; two rounds costs five, which is most
of the six-round ceiling.

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
reviewed at all. Against 32 that does not fit, and it should not be written as
though it does.

**That arithmetic assumes `review scope: all`.** It was written when there was
no scope filter, and the filter roughly halves it: with scope `own`, a queue of
twenty-odd is closer to ten, and most of a first pass fits inside the budget.
(The budget is **32 in both modes**. What differs is where reviewing stops: at
28 in `build`, leaving the four-round reserve for step 3, and at 32 in
`review-only`, where there is no step 3 to reserve for. The reserve is a
stop-reviewing threshold, not a smaller budget — the log still counts against
32.) So the two conclusions below — that step 3 does not happen, and that the
5-PR cap is unreachable — stop following. **Re-derive both for the scope you
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
not happen, and the 5-PR cap is not reachable.** Twenty-odd PRs at one to two
rounds is 20–40 reviews, and five new PRs at three each is another 15; there is
no reading of a 32-round budget on which both fit. Priority order gates ticket
work behind a queue this document says the budget cannot finish, so ticket work
waits for a night that starts with the queue already marked. That is the
intended trade — the queue is the bottleneck, not ticket supply — but it should
be read as a consequence, not discovered at 4am.

**Reserve four reviews for step 3 anyway.** Stop reviewing at 28 rather than 32,
so a night that *does* clear the queue early can still open one ticket and cycle
it. Without a reserve, step 1 always consumes everything and step 3 is dead by
construction rather than by circumstance.

**In `review-only` mode the reserve does not apply** — reviewing stops at 32, not
28. The reserve exists to protect step 3, and there is no step 3 to protect; four
rounds withheld for a step that cannot run are four rounds that simply go unspent.
Note what this is and is not: the budget stays **32**. Nothing here raises a cap,
and nothing here licenses raising one — if the ceilings make this mode
impractical, say so in the report rather than widening them.

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

The semi-cold round is not defended on cost — it is defended on what silence
can and cannot establish. It asks a narrow question a reviewer can actually
answer: does this commit close this finding? Re-sampling the whole diff does not
answer that question however many times it is repeated, which is why fixes are
closed by a round pointed at them and settling still needs a cold one.

**Two knobs, and they are not interchangeable.** The **ceiling** (six rounds per
PR) exists for fairness: it stops one pathological PR eating a night that twenty
others are queued for. The **budget** (32 rounds per run) sets total depth
across the queue. If runs are finding real problems and you want more review,
raise the budget — raising the ceiling only buys more passes over whichever PR
is already the worst-behaved. Hitting the ceiling is cheap anyway: the findings
are still written into the log, the report and the PR's marker comments, and
only the *settling* is deferred to a human or a later run.

None of this proves a PR clean. A cold subagent is unanchored but still the
same model with the same priors, so a new round is a different pass, not an
independent one: it finds *different* things, not *all* things, and the returns
diminish across rounds without reaching zero. That is exactly why there is a
ceiling and not just a clean bar. This cuts how many rounds a human has to run
by hand; it does not answer when a PR is actually done.

**3 — Only when 1 and 2 are clear**, take one ticket from "This run".

**This step does not run in `review-only` mode**, and neither does anything under
[Working a ticket](#working-a-ticket). Everything else below still does — in
particular [Filing cards](#filing-cards-for-out-of-scope-findings), because
reviewing is exactly when out-of-scope problems surface, and the 6-card cap
applies in both modes.

### Working a ticket

1. **Plan first.** Read the card and the code it touches. Write the plan into the
   log: approach, files, migration if any, tests, and what could go wrong.
2. **Cross-check the plan before implementing.** Spawn a subagent to review it
   against the actual repository, looking for stale assumptions about repo state,
   a migration number that collides, tests or CI steps that already exist, and
   anything the plan asserts without verifying. Record what it said — including
   when it disagreed and you proceeded anyway, with your reasoning.
3. **Implement** on a branch named for the card.
4. **Run the full gate.** Every step `ci.yml` runs:

   ```
   ruff check api/
   mypy api/app --ignore-missing-imports
   ruff check ml/eval
   mypy ml/eval --ignore-missing-imports --explicit-package-bases --namespace-packages
   python -m pytest ml/tests/
   cd api && python -m pytest tests/
   ```

   For web changes, all three — the test step is easy to forget:

   ```
   npm run lint --workspace=web
   npm run typecheck --workspace=web
   npm run test --workspace=web
   ```

   **On tool versions.** `ci.yml` installs `ruff mypy pytest` **unpinned**, so CI
   resolves whatever is latest on the day it runs. That means there is no pinned
   version to match — the closest you can get is installing the same unpinned way
   CI does, which the environment's setup script now handles. Do not claim a gate
   matches CI's tooling; state the versions you actually ran in the report.

   This is not a detail. The first run's reviews were checked with different
   `ruff`/`mypy` than CI used, which the agent flagged against its own work —
   CF-92 (#255) exists to pin them. **Once that lands, match the pinned versions
   and drop this caveat.**

   Do not open a PR if any gate fails — log it and move on.
5. **Open a draft PR** following `.github/pull_request_template.md`, including
   the bare `Closes #<issue>` line `CLAUDE.md` requires. Then go back to step 1:
   a PR you just opened has no review yet, and getting it reviewed — by a cold
   subagent, never by you — is your job. Draft status does not exempt it.

**Size discipline.** If a ticket would produce a diff too large to review in one
sitting, do not implement it. Write the plan into the log instead — a good plan
beats a half-finished 2000-line PR.

### Filing cards for out-of-scope findings

You will notice real problems that do not belong in the work at hand. File those
rather than fixing them inline or letting them evaporate.

**File** for: a bug, a security issue, a stale comment that would mislead the next
reader, missing coverage on something that matters, a premise no longer true, or
work a review surfaced that is bigger than that PR.

**Do not file** for: vague code smells, style preferences, or anything fixable
inline in under a minute.

- Title `CF-<n> · <what it is>`. Get `<n>` from the **highest existing CF number**
  across all issues — it has drifted from the issue numbers, so do not infer it.
- Match the house style of existing cards: what, why it matters, evidence with
  file and line references, options where there is a real choice, acceptance.
  CF-224 (#224) and CF-239 (#242) are good models.
- Labels from the existing set, including a priority.
- **Do not try to add the card to the `ClipFarm Backlog` project — a project
  workflow adds new issues automatically, with `Status: Todo`.** Verified: every
  card the first unattended run filed reached the board this way, without the
  `project` scope. Nor is there anything to do about **Sprint**: iteration
  fields are untouched by GitHub's built-in workflows, so it starts unset, which
  is what these cards want — they are for triage, not for silently joining the
  current sprint.

  So **do not report cards as missing from the board.** Every run so far has
  reported that, and it has been wrong each time. If you want to check rather
  than assume, `gh project item-list` reads with `project` scope; if you do not
  have it, say the board was unverified rather than saying the cards are off it.
- Open the body with: `Filed unattended during an overnight run — needs triage.`

### When a command is not available

You may be running in a sandbox rather than on a maintainer's machine. Docker in
particular may be absent, so anything needing `docker compose` — the local stack,
the eval harness — simply cannot run.

**Log that it could not run, and move on. Never report a gate as passing when you
could not execute it.** If a ticket's verification is impossible in the
environment you are in, write the plan and the reason into the log instead of
opening a PR.

### Repo traps that have already cost time

- Migration numbers collide. Check `api/alembic/versions/` for the current head;
  never assume.
- `api/tests/` exists and CI runs it. Test-only dependencies go in
  `api/requirements-dev.txt`, never `requirements.txt` — that file builds the
  production image.
- CF numbers have drifted from issue numbers. Check the highest existing `CF-`
  number; do not infer it from the issue count.
- A closed issue may be `COMPLETED` or `NOT_PLANNED` — opposite facts behind the
  same `state`. Always read `stateReason`.

### Reporting

Post the run report as a GitHub issue titled `Overnight run — <YYYY-MM-DD>`,
labelled `chore`. It joins the project the same way cards do — automatically,
with Sprint unset — so there is nothing to add by hand and nothing to report as
missing. A log file in a sandbox is a report nobody reads; the issue is the
copy that arrives.

**Name the mode in the title when it is not `build`** —
`Overnight run (review-only) — <YYYY-MM-DD>`. The point is that the title should
say what the run was *for*: a reader scanning issues can otherwise not tell a
night of queue work from a night of ticket work without opening it.

It also separates two runs of different modes on one date. It does **not**
separate two runs of the *same* mode on one date — nothing here does, and if
that happens, disambiguate in the title however makes sense at the time and say
in the first line which run this was.

**State the mode and the review scope in the first line of the report**, whichever
mode ran. A reader cannot otherwise tell "reviewed nothing new" from "was not
looking".

Put the same summary at the top of `.claude/overnight-log.md`.

The report contains:

- **Which tool you used for GitHub state** — `gh`, MCP, something else — and
  whether you confirmed it paginates. If you could not confirm it, say marker
  reads were unverified
- **Any footer text appended to your posts that you did not write**, quoted
  verbatim, once
- **Whether the board was verified**, and if not, say so rather than saying
  cards are missing from it
- PRs reviewed, how many rounds each took and of which kind, and findings by
  tier
- PRs labelled `unsettled`, split by the three reasons their `unsettled:`
  comment gives — `needs a decision` (only these want a human), `not our branch`
  (the author's next push re-opens it), and `ran out of rounds` (the per-PR
  ceiling, or the run-wide budget) — and what is still outstanding on each
- **Which PRs hit the ceiling or the budget**, whatever reason they ended up
  labelled with. A PR that hit the ceiling *and* carries a judgement call is
  filed under `needs a decision`, so the reason breakdown above is no longer a
  reliable count of what the bounds bound — say it separately or the signal is
  lost
- Whether the review budget ran out, and which PRs never got a first round at
  all — with a deep queue this is the expected shape of a run, not a failure
- **Which PRs the `review scope` filter excluded**, and the scope the run used.
  Keep this **separate from the bullet above**: "never got a first round" means
  the run ran out before reaching it, and "out of scope" means it was never going
  to. Collapsing the two is exactly the confusion
  [Scope](#scope-whose-prs-get-reviewed) forbids, and that distinction is the
  whole safety argument for defaulting the filter on
- PRs opened, with card and branch — and **what to test to verify each one**
- Cards filed, one sentence each on why
- Tickets attempted but abandoned, and why
- Every decision needing a human call
- Anything that failed, **verbatim** — do not summarise errors away

**In `review-only` mode two of those bullets are structurally empty** — PRs
opened (with the what-to-test notes that belong to it) and tickets abandoned.
Say "none — review-only run" rather than dropping the headings: an absent section
reads as an oversight, and the next run's reader cannot tell which it was. Cards
filed is **not** one of the empty ones.

Be honest. A report that overstates what landed is worse than a short one.
