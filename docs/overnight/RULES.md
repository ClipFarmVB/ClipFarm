# Standing rules

Read on **every iteration**. The rules that apply no matter which step the lap is doing — what is forbidden, what counts as evidence, what may be pushed to, what must be logged, and the counters that decide whether the run continues.

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

> **Update `START.md` before starting.** Everything in this file and the phase
> files holds every time. `START.md` does not, and an agent given a stale scope
> will work confidently on the wrong things. This repository has been bitten by
> exactly that: CF-192's worst-case reasoning was invalidated by CF-224 without
> the text changing, and CF-224 read as "fixed" while production was still
> failing.
>
> **Nothing that is discardable may carry a rule.** `START.md` holds scope —
> what tonight's environment looks like, and any narrowing of it — and never
> behaviour. If a run learns something that should change how future runs act,
> that belongs in this file or the phase file it governs, even when the lesson
> came from tonight's scope. A fix written into a section the next reader is
> told to replace has not been made: it will be discarded unread, while whoever
> wrote it believes it landed. That happened twice in the first real run, and
> one of the two was reported as done.
>
> This is also why the brief is split by phase rather than summarised into a
> shorter file: a summary is a second copy of every rule, and the copy that is
> not amended is the one someone reads.

### Hard rules

- **Never** push to `main`, merge a PR, or force-push anything.
- **Only push to the branch of a PR opened by the account this run posts as.**
  Any run's PR, not just this one's. If a fix belongs on a PR **another account**
  opened, describe it in a review comment instead and never push. See
  [The push test](#the-push-test) below.
- **Never authorise your own push past [The push test](#the-push-test).** If it
  says the PR is another account's, that is the answer — do not post, label or
  record anything that would let a later round read it as permission. A grant
  mechanism existed once and is gone (CF-274); this rule is about the class, not
  that mechanism, and holds whether or not one exists again.
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
- **Maximum 6 new PRs** and **7 new cards** per run.
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

### Evidence, and the higher bar for rejecting a finding

**Quote only primary source you actually fetched.** Every timestamp, line
number, SHA and count in a reply that disputes or dismisses a round's finding
must come from a call made *for that reply*. A number recalled, inferred from
nearby context, or carried over from an earlier lap is not evidence, and
presenting it as one is how a correct finding gets discarded.

The run of 2026-09-01 rejected a true finding on #451 this way. A semi-cold
round said the PR body still carried two overclaims; the reply answered that a
body edit had preceded "your marker comment at `06:31:36Z`", so the round had
read a stale copy. The marker is at **`06:27:06Z`**, and `06:31:36Z` exists
nowhere on that PR — the nearest value is `06:31:20Z`, the reply's own
timestamp. The comments endpoint had never been called.

**The retraction then did it again, which is the part to learn from.** It
carried the PR's `updated_at` of `06:28:01Z` forward from the dispute, called
it "the body edit", and derived that the edit landed 55 seconds after the
marker. That figure had been fetched at some point — but not for that sentence,
and it is not what the sentence called it: `06:28:01Z` is the `submitted_at` of
the round's own **review**, which is what bumped `updated_at`. Reaching back
for a number already in the thread is how the second instance happened; a
figure that answers a new question needs a call made to answer it. A body edit's time is not recoverable through REST at all, so the
derivation had no source and the ordering it asserted remains unknown. Fetching
a number is necessary and not sufficient; it also has to be the number the
sentence says it is. A correction written under scrutiny, about this exact
failure, reproduced it in one step — so treat the first retraction of a claim
like this as the likeliest place for the second instance, not the safest.

**The failure is one step earlier than "verify claims against the repository",
which is why that rule did not catch it.** The run believed it was citing a
measurement. What made the paragraph persuasive — to the round it answered, and
to the run itself — was its *shape*: a table-ready figure, quoted to the second.
Formatting is not fetching.

**So fetch a dispute's sources in the same breath you write it**, where an
agreement can lean on a reading taken earlier. The asymmetry is the reason: a
wrong finding you accept costs a needless fix, and the change is there for the
next round to see. A right one you reject costs the finding.

### The push test

**One condition: this account opened the PR.** Compare `gh api user --jq ".login"`
against the PR's `.user.login`. That is the same test the `review scope` filter
runs, so at `review scope: own` it is true of everything in the queue.

```
ME=$(gh api user --jq ".login")
AUTHOR=$(gh api repos/ClipFarmVB/ClipFarm/pulls/<n> --jq ".user.login")
[ -n "$AUTHOR" ] || { echo "cannot read the PR author — do not push"; exit 1; }
[ "$AUTHOR" = "$ME" ] || { echo "another account's PR — do not push"; exit 1; }
```

**Guard the empty read.** A PR always has an author, so an empty `$AUTHOR` means
the call failed — a network error, a rate limit, a wrong PR number, a token
missing a scope — not that the PR is unowned. Without that check the comparison
against an empty string is simply false and the guard *looks* like it fired for
the right reason. Both guards **exit**; one that only prints lets the push it
detected go out anyway.

*Phrased as PR authorship rather than branch ownership because authorship is what
the test reads.* GitHub does not expose "who owns the branch", and a rule written
in terms its test cannot evaluate is a rule that drifts from its enforcement.

#### The second condition, and why it is gone

Until 2026-08-29 there was a second condition — **nobody else has pushed to the
branch** — tested by reading `.author.login` off every commit in
`pulls/<n>/commits` and refusing on any login but this account's. It was there to
stop the run landing fixes on a collaborator's in-flight work, since a
collaborator can push to a branch whose PR this account opened.

**It was removed because it does not test that.** Commit authorship records where
code *came from*, not who is holding the branch, and the two are decoupled by
every ordinary history operation — rebase, cherry-pick, replay, squash. A branch
this run creates by replaying someone else's commit carries their authorship
forever, and reads to the guard exactly like a branch they are actively working
on. The two cases are byte-identical in the data, so no threshold or refinement
of that query separates them.

Measured on the queue of 2026-08-29, eleven PRs all opened by this account:

| commit authorship of the PR's own commits | PRs |
|---|---|
| the bot identity every Claude-written commit carries | five |
| a teammate's, from commits **this run replayed onto a fresh branch** | three |
| both | two |
| this account's own | one |

Ten of eleven refused. Not one was a collaborator working on the branch: five
were the identity this environment stamps on everything it writes, and five
carried preserved authorship from rebuilds this account had performed itself a
day earlier. The guard's entire yield was false positives, and it made the loop
unable to fix code on any PR in its own queue — the same stall CF-270 removed,
arriving by a different route.

**Excluding the bot identity was considered and rejected.** It would have cleared
five of the ten and left the five replayed-authorship refusals untouched, because
those name real people. It also cannot be right in principle: as more of the
repository is written through Claude, a genuine collaborator's commits carry the
bot identity too, so the exclusion blinds the guard to exactly the case it exists
for while still firing on rebases. A test that is wrong in both directions is not
improved by narrowing it.

**What the removed condition protected is real but narrow**, and worth stating so
it is not silently assumed handled: a teammate with the branch checked out gets a
rejected push, or force-pushes over a fix, if the run writes to it underneath
them. Nothing is lost — git keeps both sides — but it is disruptive and nobody
asked for it.

**If that hazard is ever worth a guard again, the signal is the pusher, not the
author** — who most recently pushed to the ref, which GitHub exposes separately
from commit metadata and which no history rewrite launders. That is a different
rule and it should be written only when the hazard has actually bitten, with the
incident named. Do not reinstate an authorship check.

**Two consequences to hold on to.** A branch a collaborator is working on is now
pushable as far as this document is concerned, so **read the PR before pushing to
it** — a conversation about work in progress is a reason to write a comment
instead, and that judgement now sits with the run rather than with a query. And
the run may now push to a branch carrying commits it did not write, so the head
SHA on every marker is doing more work than before: it is the only thing left
that detects a head moving under an open round.

**If the harness refuses the push, that is a separate gate and this rule does not
override it.** Report the refusal rather than working around it, and label the PR
`unsettled: latched @ <sha>` — see [the terminal labels and their
reasons](REVIEW.md#the-terminal-labels-and-their-reasons), where `latched` is now
defined by that refusal rather than by a collaborator's commits.

*This was "branches this run created" until 2026-08-25.* That rule was
conditioned on a sign-off it never received, and the cost was measured: of the
eight PRs in one night's working set, seven ended `unsettled: not our branch`
and **all seven were this account's own work from earlier runs**. The loop could
review everything it had built and fix none of it. The sign-off is now given:
earlier runs of this account are this account.

### Log before you finish each iteration

Append a dated section to `.claude/overnight-log.md`: what you did, what you
decided and why, and anything needing a human call. **Read it at the start of
every iteration.** Context may be compacted between iterations; the log is the
only thing that survives.

**An iteration is not finished until the next one is scheduled *and the
schedule is verified*** — unless the run is stopping under one of the hard rules
above, in which case the closing log line says which one, in those words. Both
endings look identical from outside, so the log entry is what tells them apart;
an unscheduled lap with no such line is a stall, not a decision.

**Verify by reading the schedule back** — list the pending triggers and confirm
one exists — and treat the scheduling call's own success as no evidence. The run
of 2026-08-30 stalled three times: twice because a scheduler accepted a wake-up
and never fired it, and once because the lap simply ended without the call being
made, while the instruction to make it sat in the prompt being executed. The
first two shared a cause worth recognising — a `stop` issued in an *earlier* run
had terminated the loop, so every later wake-up was accepted and inert, and the
laps in between were actually driven by subagent notifications. That is why it
only stalled when nothing was in flight.

**Do the schedule-and-verify before writing the iteration's closing summary**,
not after. A long summary is exactly what pushes the last call out of a turn.

**One thing goes in at the start of the run, not the end of an iteration:** the
run's own start time, in this shape, on a line of its own:

```
run start: 2026-08-25T04:12:09Z
```

**Find it by matching the line, never by reading the log's first line.** The
first line is not load-bearing and nothing guarantees what sits there — an
iteration that appends before the count runs, or a partly-written entry, owns it
just as easily:

```
SINCE=$(grep '^run start: ' .claude/overnight-log.md | tail -1 | cut -d' ' -f3)
[ -n "$SINCE" ] || { echo "no run start in log"; exit 1; }
```

`tail -1`, not `grep -m1`. The log is truncated at the end of each run — see
[Reporting](REPORTING.md#then-reset-the-log-and-only-then) — so it should hold
exactly one `run start:` line and the two would agree. Take the last anyway:
a run that died before its reset leaves the previous run's line above this
one's, and `grep -m1` would then window this run's counts against a night that
is already over. And guard the empty case —
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
[the counting windows](#logging-and-the-counting-windows). Write it before the
first iteration does
anything.

### Priority order

Finish work already in flight before starting anything new.

**Steps 1 and 2 are the two halves of one PR's cycle, not two sweeps over the
queue.** Read them as: pick a PR that needs a review, then carry *that* PR
through review and fix and re-review until it reaches a terminal state, then
pick the next one. Running step 1 across every open PR and only then starting
step 2 is the breadth-first pass ruled out below.

#### The ceiling, and the settling exception

**Ceiling: seven rounds per PR per run, cold and semi-cold together**, so a
pathological PR cannot consume the whole night. Counting only cold rounds would
leave the semi-cold ones unbounded — every fix buys another check — and half of
a ceiling is not a ceiling. Seven covers a PR with two rounds of findings and
the cold round that settles it — five by the cost model below, with two spare.
The first spare is allocated: a PR that lands on the routing table's open-finding row
spends it on the semi-cold round that recovers from a clean marker posted over
an unclosed finding. The second was added after the run of 2026-08-30, where
#438 took five rounds because each fix drew a finding one spelling further out;
it converged, and would have been cut off at six. A PR needing the detour twice
*and* a third fix cycle still hits the ceiling, which is the intended outcome —
that is no longer converging.
Hitting it is the same outcome: fix what you can, apply `unsettled` with an
`unsettled: ran out of rounds @ <sha>` comment, record, move on.

**One exception: a PR with nothing open may run the rounds settling needs, past
the ceiling.** If the last round leaves no Critical and no Medium outstanding,
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

**These rounds are charged to the 40-round budget.** They are real reviews and
the counting query charges them automatically; unlike a `reopened:` marker or a
re-posted marker, nothing here is free. The exception lifts the *per-PR*
ceiling, never the run-wide budget.

#### Order of work: one PR at a time

**Take one PR all the way through before opening the next.** Review it, fix it,
check the fix, settle or label it — then move on. Do not run a pass over every
open PR and come back for a second lap.

The reason is that this loop gets interrupted: context is compacted between
iterations, and a usage limit stops the run outright, at no point of your
choosing. Finishing PRs one at a time means whenever that happens, everything
touched so far is in a terminal state — `review-settled`, `unsettled`,
untouched, or reviewed-clean-but-held-back-by-a-check, which [carries no label
deliberately](FIX.md#the-cycle-and-the-settle-bar) — and the next run can tell
those apart. A breadth-first pass that is
cut off leaves every PR half-cycled, which is precisely the "abandoned
mid-cycle looks identical to reviewed clean" condition these labels exist to
prevent. It also keeps the state you carry small: one PR's findings, not twenty.

The cost is real: if the run dies early, PRs at the back of the queue got
nothing at all. So the order matters. Take them: PRs this run opened, then any
carrying a priority label, highest first, then oldest first. Note that most open
PRs carry no labels at all, so in practice this is mostly "oldest first" — which
is the intent, since the oldest have waited longest. Do not order by the
`overnight-ok` label: that is the *issue* selection gate from
[Choosing work](START.md#choosing-work) and no PR carries it.

#### The run budget

**Run budget: 40 rounds per run**, cold and semi-cold together. *Rounds*, not
reviews: each round now submits a GitHub review as well as posting its marker,
so counting "reviews" would be ambiguous about which artifact is meant. The
budget counts rounds, and a round is one marker comment. Seven rounds
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

#### Logging, and the counting windows

**Log every round as you finish it** — `PR #<n> — <cold|semi-cold>, round
<k>/7, budget <used>/40` plus the tiers found. A round granted by the settling
exception is logged as `settling, budget <used>/40` instead of a `<k>/7` — it is
outside the ceiling, and writing `8/7` reads as a counting bug to the very
cross-check that is meant to catch one. Neither bound is enforceable
unless the count survives: context may be compacted mid-run, and counts you hold
in your head reset to zero when it is. Recover both from the log at the start of
every iteration, and cross-check **both** counts against the markers — the
per-PR round count, and the run-wide budget, which is the sum of this run's
markers across every PR it touched:

```
ROUNDS='^(cold: (findings|clean)|semi-cold: (closes|does not close)) @ ?[0-9a-f]{7}'
for n in $(gh pr list --state open --json number --jq '.[].number'); do
  gh api --paginate "repos/ClipFarmVB/ClipFarm/issues/$n/comments" --jq ".[] | select(.created_at > \"$SINCE\") | select(.body | test(\"$ROUNDS\"; \"i\")) | .id"
done | wc -l
```

The budget needs this as much as the ceiling does. Recovering it from the log
alone leans on the one source this same paragraph says a compaction can lose
entries from, and losing entries makes the budget read *low* — so the run keeps
reviewing past 40 and starves step 3, failing toward more reviewing rather than
less.

When log and markers disagree, **the markers win.** The log records
what a round intended; the markers record what the PR actually carries, and
every other rule here reads the PR. A log ahead of the markers means a round's
marker did not land, which the check above is there to catch at the time; a log
behind them means a compaction lost an entry. Neither is a reason to trust the
log over the thing the rules read. Count markers, not comments: comments also
carry your step 2 fix replies and anything a human wrote.

**Count only markers from this run.** Markers persist for the life of the PR;
the ceiling is seven rounds *per run*, and an `unsettled: ran out of rounds` PR is
promised a reset when new commits land. A raw count undoes both — a PR that
spent seven rounds last night would read as already at the ceiling before this run
touched it. So count markers newer than the run's start time, which the
[logging rule](#log-before-you-finish-each-iteration) puts on its own
`run start: ` line — found by matching that line, never by position.

**When a PR was re-opened mid-run, count from the `reopened:` marker instead —
but only if that marker falls inside this run.** There is no label event to
read here; re-opening writes that marker precisely so this bound survives the
label being removed. Three states carry a commits-since carve-out —
`review-settled`, and the `ran out of rounds` and `not our branch` reasons for
`unsettled` — and each re-opens the same way, so each gets the same bound.
(`needs a decision` and `latched` have no carve-out and never need it: both
wait for a human, and neither is cleared by anything a run can do.) The bound
you want is the *later* of the run start and that marker: a `reopened:` marker
from last night is older than the run start, so counting from it sweeps in
markers this run has already spent and the ceiling arrives early on a PR just
promised a reset.

`.created_at > "$SINCE"` is a lexicographic string compare against GitHub's
`2026-08-24T23:08:57Z`, so `SINCE` must be UTC with the `Z` suffix and nothing
else — which is what `date -u +%Y-%m-%dT%H:%M:%SZ` produces, and why the run
start is recorded in that form. An offset form like `2026-08-25T01:08:57+02:00`
sorts wrong against it and the count comes back low or zero — which reads as "no
rounds this run" and hands the PR a fresh seven-round ceiling:

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

### Repo traps that have already cost time

- Migration numbers collide. Check `api/alembic/versions/` for the current head;
  never assume.
- `api/tests/` exists and CI runs it. Test-only dependencies go in
  `api/requirements-dev.txt`, never `requirements.txt` — that file builds the
  production image.
- CF numbers have drifted from issue numbers. Check the highest existing `CF-`
  number; do not infer it from the issue count.
- **A squash merge carries every commit message onto `main`**, so a `Closes #N`
  in a commit *body* is landed on the default branch and closes that issue —
  whatever the PR body says. **Measured here:** every squash sampled on `main`
  carries its commits' full bodies (8 checked, 21–368 lines each). **Not
  measured here:** that a commit-body keyword closes an issue the PR body does
  *not* name. `0acc05a` carries `Closes #293` and closed it — but #404's PR body
  says `Closes #293` too, so it cannot tell the two mechanisms apart, and the
  repository holds no discriminating case. GitHub documents the behaviour; treat
  it as documented, not demonstrated.

  Two consequences hold either way. **Get the closing reference right in the
  first commit**, because retargeting it later means rewriting a pushed message.
  And when you do retarget one, `grep` the *commit messages* as well as the PR
  body — the run of 2026-08-30 fixed the body, left the commit, and would have
  closed a card whose remaining content was a decision nobody had made. It is
  easy to miss in review: the operator could not see the line at all, because
  the Commits tab shows subjects until a commit is expanded.

  **Warn in the PR body, at the top — and say what the clean fix would be.** The
  warning is the part a run can do unaided, and it lands where the person
  merging sees the editable squash body. The clean fix is an amend and a
  force-push, which [the hard rules](#hard-rules) forbid; naming it is not the
  same as taking it, and **nothing here is a standing permission** — an operator
  lifting that prohibition once does not license a later run to assume it, or to
  read this paragraph as a grant. That is the rule directly above about never
  recording anything a later round can read as permission.
- A closed issue may be `COMPLETED` or `NOT_PLANNED` — opposite facts behind the
  same `state`. Always read `stateReason`.
- **The clone may be shallow, and a shallow clone fakes a clean merge check.**
  `git merge-tree <base> <head> | grep -c '^<<<<'` returns `0` when the command
  produced *no output at all*, which is what a missing history looks like — and
  `0` reads as "no conflicts". Run `git fetch --unshallow origin` before
  believing any merge or `origin/main..` comparison. A real conflict was hidden
  this way.
- **Stale `__pycache__` makes a mutation look like it survived.** Before every
  mutation run: delete `__pycache__` and export `PYTHONDONTWRITEBYTECODE=1`.
  The direction matters — stale bytecode can only produce false *survivals*,
  never false kills, so an unexpected "the test still passed" is the case to
  distrust.
- **A mis-anchored substitution prints a clean pass indistinguishable from a
  survival.** Every mutation must assert two things: the anchor appears exactly
  once, and it was actually applied. Restoring is the restore bullet below. Two
  mutations "passed" this way before that check existed — an em dash silently
  became a double hyphen, and a 12-space anchor matched the tail of a 16-space
  line.
- **Apply edits one at a time, never as a batch script.** A five-edit script
  that asserts partway through writes nothing, while the verification run after
  it looks entirely normal.
- **Changing a number in this brief means hunting it in words, not only in
  tokens.** These files argue in prose, so a value lives as `32 rounds` *and* as
  "against 32", "five new PRs", "the 5-PR cap", "writing `7/6`", "past 32". CF-365
  raised four caps, grepped only the format strings — `32 rounds`, `six rounds
  per`, `/6, budget` — and left **seven** prose survivors for a reviewer to find.
  Every one of the greps was a format string; every survivor was a sentence.
  `grep -rn '\b32\b\|\bsix\b\|\bfive\b' docs/overnight/` and reading the hits
  costs about a minute and catches all of them. Two cautions with it: most hits
  are *historical* — "#307's six rounds against red CI" is a record, not the cap,
  and rewriting it turns history into a lie — and a raise can invalidate an
  **argument** rather than just a number. CF-365's own budget arithmetic
  ("forty-odd against 32 does not fit") stopped following at 40, and needed a
  paragraph rather than a digit.
- **The restore step is where mutation testing goes wrong, not the mutation.**
  Two restores destroyed work in one run: a checksum caught one, and a moved
  test count caught the other. Neither announced itself — the mutation's own
  result looked exactly as expected both times. Never mutate a string to the
  empty string:
  replacing `""` back inserts the text at position 0, and a file began
  ` group-hover:bg-brand/20import { Clapperboard } …`. Never restore with
  `git checkout -- FILE` when the file carries uncommitted edits; it silently
  deleted them, noticed only because a test count moved 148 to 147. Copy the
  file first, restore with `cp`, and verify with `cmp` — not by eye and not by
  `git status`, which says nothing about a file you have deliberately changed.
- **`search_issues` silently under-reports** — it is semantic matching, by its
  own description, not literal search, and the failure looks like a fact. A
  title query for existing run reports returned `0` and later `1`, against 5
  that exist. Cross-check any negative or small result against `list_issues`
  before stating an absence; "I found none" and "there are none" are different
  claims.
- **Tag-shaped text is deleted from anything you post, backticks or not.** Not
  angle brackets in general: measured, `ComponentProps<"a">`, `a <= b` and
  `x < y > z` all survive escaped, while a placeholder shaped like an HTML tag
  is removed — inside backticks and outside. A run report's command posted as
  `git checkout -- `, truncated with nothing to say it had been cut. Use a plain
  word for placeholders in issues, comments and PR bodies; files in the repo go
  through a different path.
