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

Quoting only what you fetched is not a rule about disputes. It holds for
everything a run publishes, and a disputing reply is only where it costs most.
The carve-out below is not an exception to that: *agreeing* with a finding may
lean on an earlier reading, because a needless fix is cheap and visible, while a
wrongly rejected finding is not.
[Measure what you publish](#measure-what-you-publish) is the general form, and
lists what it has cost.

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
figure that answers a new question needs a call made to answer it. A body
edit's time is not recoverable through REST at all, so the derivation had no
source and the ordering it asserted remains unknown. Fetching
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

#### Pushing to a branch that may have moved

With several PRs cycling at once (CF-564), a fix can take long enough that
someone else pushes to the branch first — a collaborator, or the operator on this
same account. **Every push to a PR branch goes through this guard**, where
`STARTED_AT` is the full SHA of the head the work was built on and `BRANCH` is
the PR's branch:

```
# push guard (CF-564)
REMOTE=$(git ls-remote origin "refs/heads/$BRANCH" | cut -f1)
[ -n "$REMOTE" ] || { echo "cannot read the remote head - do not push"; exit 1; }
[ "$REMOTE" = "$STARTED_AT" ] || { echo "head moved - do not push"; exit 1; }
git push origin "HEAD:$BRANCH"
```

**Why `ls-remote`, not a fetch.** It reads the branch straight off the remote,
and a read that fails prints nothing, which the second line refuses — the guard
fails closed by what it says. The first version fetched and compared
`FETCH_HEAD`; review asked whether a failed fetch would leave a stale
`FETCH_HEAD` equal to `STARTED_AT` and wave the push through. Tried on
2026-09-27 with the remote made unreachable: it refused, but only because git
empties `FETCH_HEAD` when a fetch begins — a property of git's implementation,
not of the guard.

**What git already refuses, and what it does not.** A plain push is rejected when
someone added commits on top: the fix is no longer a fast-forward. It is
**accepted** when someone *rewound* the branch — pushed it back to an ancestor, to
drop a bad commit, say — because the fix still descends from the new tip, and the
push silently restores the commit they removed. The guard catches both, by
comparing against the head the work started from instead of asking git whether
the push fits. `api/tests/test_overnight_push_guard.py` runs this block as
written against both, and shows that a plain push lets the rewind through.

**What it leaves open.** Between reading the remote head and the push landing —
a short window, the push's own round trip included — the branch can still be
rewound, and the push would then succeed.
`--force-with-lease` with an explicit expected SHA would close that window
atomically and is deliberately not used: [the hard rules](#hard-rules) forbid
force-pushing, and a rule that reads "never force, except this flag" is how an
exception grows. The consequence is a removed commit reappearing on the branch —
visible, and nothing lost.

**When the guard refuses: never rebase, merge or force.** Each would either
discard someone's commits or fold in commits no round has read. Stop, and route
the PR as [`head moved`](FIX.md#when-you-cannot-fix-it-choosing-a-reason) — or,
if it could not read the remote at all, route the target again once it can.

**When the guard passes and the push is still rejected, that is not `head
moved`.** The branch had not moved; something refused the push itself — branch
protection, a hook, the harness. That is
[`latched`](FIX.md#when-you-cannot-fix-it-choosing-a-reason), and the difference
matters: `head moved` re-opens on the next commit, and whatever refused this
push will refuse the next, so it would cycle the PR forever — the loop `latched`
exists to prevent. And **the fix now exists only in the fixer's worktree**, so
that worktree is kept at the end of the run rather than removed, and named in the
report; the one retry [the latch rule
allows](REVIEW.md#record-comments-human-removal-and-re-opening) runs from it.

### Dispatching subagents

Ported from the `Solo_Hack` / `HTN_WHITEOUT` build harness (CF-561), which runs
several implementers at once. **None of this changes anything while one subagent
runs at a time. It is what makes more than one safe**, and it is here rather than
in `BRIEFS.md` because more than one phase spawns: a review round spawns, and so
does the plan cross-check in [Working a
ticket](TICKETS.md#working-a-ticket).

- **Every subagent gets worktree isolation** — reviewers, fixers, implementers,
  and the cross-check alike, not only the ones that write. A subagent that reads
  the orchestrator's checkout is reading a tree anything else may move.

  The failure this prevents is silent, which is why it is a rule and not a
  preference: two subagents sharing one checkout **switch each other's
  branches**, and each then reports confidently on a tree that is not the one it
  was told to read. Nothing errors and both reports look ordinary. This is the
  same class as every other trap in this file — a wrong answer that arrives
  looking like a right one.
- **Work on an existing PR happens on a detached checkout.** The subagent runs
  `git fetch origin BRANCH`, then `git checkout --detach FETCH_HEAD`; a fixer
  pushes with `git push origin HEAD:BRANCH`. That way no worktree *holds* a
  branch another agent needs, which a plain `git checkout BRANCH` would.
- **A worktree starts wherever this session's checkout is, not at `main`.** The
  harness copies this session's current commit, and a probe on 2026-09-27 found a
  nested child starting at a commit that was neither `main` nor its parent's.
  So every subagent checks out what it reads before reading it: the detached PR
  head above for PR work, and for anything judged against `main` — a plan, a
  cross-check — `git fetch -q origin main && git checkout -q --detach FETCH_HEAD`.
  A subagent that reads the tree it was handed reviews whatever branch this
  session happened to be on, and says nothing, because the tree is consistent.
- **Spawn from the repository root**, so a relative path in the brief means what
  it says.
- **Pass the rules that bind the subagent explicitly.** Do not hand it this
  file: it opens by telling its reader that the reader is never the reviewer, so
  a subagent given `RULES.md` has been told the opposite of its job. Spell out
  what binds it — at minimum that it must not push to `main`, merge, force-push
  or deploy; must not read or echo a secret; adds no attribution stamp; prefixes
  its comments; stays inside its scope; and reports a decision it needs rather
  than taking it.

**When the environment gives no worktree, run one subagent at a time and say so
in the log and the report.** That is the capability check in
[`START.md`](START.md#first-establish-what-you-can-actually-do), and it is not
optional: at one subagent the rules above cost nothing, so there is no reason to
proceed without isolation rather than degrade to serial.

**This session's own checkout stays on `main`** (CF-563). It no longer
implements anything itself, so nothing needs a branch here, and every worktree
the harness makes starts from whatever this checkout has checked out — a
feature branch left here would become every subagent's starting point.

#### The registry

**Every dispatch appends a line to the log, and every completion appends
another** (CF-562):

```
dispatching: ROLE TARGET UTC
dispatched: ROLE TARGET AGENT UTC
finished: ROLE TARGET AGENT UTC OUTCOME
```

`ROLE` is what the subagent was spawned to do — `cold`, `semi-cold`,
`fixer`, `planner` or `implementer`. `TARGET` is `#<n>`, the PR or issue it works on. `AGENT` is the
id the harness returned for the spawn. `UTC` is a resolved timestamp, never the
command that produces one ([Measure what you publish](#measure-what-you-publish)).
**A `dispatching:` or `dispatched:` line with no matching `finished:` line is in
flight.**

**Write `dispatching:` before the spawn and `dispatched:` straight after it
returns**, in the same turn. The agent's id does not exist until the spawn
returns, so the second line cannot come first; the first line exists so that a
compaction landing *during* the spawn still leaves a trace. Without it, the
next lap finds nothing in flight, reads the PR as still owed a round, and
spawns a second one.

**A `dispatching:` with no `dispatched:` is that case.** Look for the agent in
the harness's list of agents this session spawned. If it is there, write its
`dispatched:` line now; if not, the spawn never happened — write
`finished: ROLE TARGET - UTC never-spawned` and route the target again.

**Never run two subagents against the same target at once.** Before spawning
anything against a PR or an issue, read the registry for an in-flight line on
it. This is not only a concern for later parallel work: laps run while
background subagents are still working — [the logging
rule](#log-before-you-finish-each-iteration) records a run whose laps were
driven by subagent notifications — and a round in flight has posted no marker
yet, so [step 1](REVIEW.md#step-1--which-prs-need-a-round) reads its PR as still
owed one. Nothing in this brief stopped that second spawn before the registry
existed. It costs a round of budget, and a fix pushed in between voids one of
the two markers by the SHA test.

#### Claims

**The registry lives in the log, and the log does not outlive the run** — it is
truncated at the end of each one. So every claim is mirrored on GitHub, where
the next run can read it:

1. **Claim before dispatching**, and before working a target yourself: add the
   `in-progress` label, then comment `claimed: <UTC>`.
2. Dispatch, then write the `dispatched:` line.
3. **Release when the target reaches a terminal state**: remove `in-progress`,
   then comment `released: <UTC> — <outcome>`.

**What gets claimed.** A **PR** is claimed from the first round of its cycle
until it reaches one of the terminal states in [Order of
work](#order-of-work-every-pr-carried-to-a-terminal-state) — `review-settled`, `unsettled`, or
reviewed clean but held back by a check, which releases with the outcome
`held by a check` and still carries no review-state label, as before. A
**ticket** is claimed from the moment work on it starts until its PR is opened,
releasing with `PR #<n>`, or until it is dropped, releasing with
`no PR — <why>`. From there the PR carries the work, and [a ticket an open PR
already closes is never selected](START.md#choosing-work) — so no ticket claim
has to outlive the run, and none does.

*It said "until its PR is closed or merged" when CF-562 was first written.*
Merging is a human act, usually after the run has ended, so that claim could not
be released by any run: the next one found it older than its start, released it
as orphaned, and the ticket became selectable again with its PR still open — a
duplicate PR waiting to happen. Two independent reviews found it.

**Why claims and not only the registry.** The terminal labels exist because
[a PR abandoned mid-cycle looks identical to one reviewed
clean](REVIEW.md#the-terminal-labels-and-their-reasons). The claim closes the
remaining gap: a run cut off by a usage limit, a compaction or the operator
leaves every PR it had in cycle carrying `in-progress`, so **mid-cycle is a
state the next run can read rather than infer**. Without the claim, the only
record of mid-cycle was the registry, and the registry is exactly what the
interruption loses.

**`in-progress` is a claim, not a review state.** It records that a run is
working on something, not what any round found, so it never replaces
`review-settled` or `unsettled`, and a PR can carry it alongside neither.

**`hold` is the human's.** A human applies it to tell the loop to leave a PR or
ticket alone. **The run never applies or removes it, and never selects anything
carrying it** — not for a round, not for a fix, not for ticket work.

**Every lap starts by reconciling this run's claims against GitHub**, because
people act on a target while the run holds it: a PR is merged or closed, a
ticket is closed, `hold` is applied. For each target this run has claimed:

```
gh api repos/ClipFarmVB/ClipFarm/issues/<n> \
  --jq '"\(.state) merged=\(.pull_request.merged_at // "-") \([.labels[].name] | join(","))"'
```

The issues endpoint answers for PRs too, and `merged_at` tells a merge from a
close. **Closed, merged or `hold`: stop any subagent working on it, and release
it** with the outcome `merged`, `closed` or `held by a human`. A slot held by a
target nobody wants worked on anymore is a slot lost for the rest of the night,
and a fixer still pushing to it is working against a person.

#### Stale claims

**At the start of a run, release every claim that is older than the `run start:`
line *and* older than the longest limit in [the table below](#stale-claims)** —
remove `in-progress`, then comment `released: <UTC> — orphaned by an earlier
run`. [Only one run may be live at a
time](START.md#what-it-may-push-to-and-what-follows-from-that), so a claim from
before this run belongs to a run that is over. **But over is not the same as
gone**: stopping a loop removes its schedule, not the background subagents it
had already spawned, and one of those can still push. A claim younger than the
longest limit is left alone, and its target is not selected, until it ages past
it. **Only release a claim whose `claimed:` comment this account posted**: an
`in-progress` label with no such comment was put there by someone else, and it
is left alone too.

```
ME=$(gh api user --jq .login)
SINCE=$(grep '^run start: ' .claude/overnight-log.md | tail -1 | cut -d' ' -f3)
[ -n "$SINCE" ] || { echo "no run start in log"; exit 1; }
LONGEST_MIN=120  # the longest "lost after" in the table below
CUTOFF=$(date -u -d "-$LONGEST_MIN minutes" +%Y-%m-%dT%H:%M:%SZ)
[ -n "$CUTOFF" ] || { echo "could not compute the cutoff"; exit 1; }
gh api --paginate "repos/ClipFarmVB/ClipFarm/issues?state=open&labels=in-progress&per_page=100" --jq '.[].number' |
while read -r n; do
  CLAIMED=$(gh api --paginate "repos/ClipFarmVB/ClipFarm/issues/$n/comments" \
    --jq ".[] | select(.user.login == \"$ME\") | select(.body | test(\"^claimed: \")) | .created_at" | tail -1)
  if [ -z "$CLAIMED" ]; then echo "#$n: in-progress, no claim of ours - leave it"
  elif [ "$CLAIMED" \< "$SINCE" ] && [ "$CLAIMED" \< "$CUTOFF" ]; then echo "#$n: orphaned, claimed $CLAIMED - release"
  elif [ "$CLAIMED" \< "$SINCE" ]; then echo "#$n: an earlier run's, claimed $CLAIMED - too recent, leave it"
  fi
done
```

The `issues` endpoint returns PRs as well as issues, which is why it is used
rather than `gh issue list`: one query covers both kinds of claim. The
comparison is the same `Z`-suffixed string compare as [the counting
windows](#logging-and-the-counting-windows), with the same requirement on
`SINCE`. `tail -1` takes the newest of this account's claims, because the
comments endpoint returns oldest first and a target claimed again after a
release carries more than one.

**The label-filtered listing can lag a label change.** Seen once, on
2026-09-27: `issues?labels=in-progress` omitted an issue labelled about five
seconds earlier, and listed it on the next call. One observation is not a
measured property, and the rule below does not need one — it only makes the
run more conservative. Harmless here, because a claim
old enough to be orphaned is old enough to be indexed. It is not harmless
anywhere a claim was *just* made, so **nothing that needs this run's own
claims — counting what is in flight, above all — reads them from a label
query.** The registry is the source for those; GitHub is the source for what
an earlier run left.

**Within a run, a subagent silent past its limit is stopped first, then
released.** Stop it with the harness's task-stop tool before doing anything
else, so it cannot post a marker or push after its target has been handed on.
Then write its `finished:` line with the outcome `lost`. A lost **round** is
spawned again, and its PR's claim stays: the cycle is not over. A lost worker on
a **ticket** releases it with `no PR — lost`.

| role | lost after | measured from |
|---|---|---|
| `cold`, `semi-cold` | 60 min with no marker | the `dispatched:` line |
| `fixer` | 60 min with no push or reply | the `dispatched:` line |
| `planner` | 90 min with no plan | the `dispatched:` line |
| `implementer` | 120 min with no PR | the `dispatched:` line |

**Where 60 minutes comes from, and what it is not.** Markers do not measure how
long a round takes; they bound it. The gap between two consecutive markers on
one PR contains a whole round, plus whatever else happened before it was
spawned — a fix, an idle lap. Measured over every PR comment from 2026-08-01 to
2026-09-27T00:00Z, keeping gaps under four hours, with inclusive-method
percentiles (Python's `statistics.quantiles(..., method="inclusive")`):

| pair of markers | n | median | p90 | max | over 60 min |
|---|---|---|---|---|---|
| cold then cold | 34 | 15.5 | 30.9 | 49.6 | 0 |
| cold then semi-cold | 84 | 11.0 | 21.1 | 190.1 | 2 |
| semi-cold then cold | 89 | 16.1 | 23.9 | 148.0 | 2 |
| semi-cold then semi-cold | 78 | 12.4 | 21.3 | 108.1 | 2 |

So 60 minutes is **a judgement, not a measurement**: about twice every p90, and
exceeded by 6 of 285 gaps, each of which may hold a round far shorter than
itself. What it costs when it is wrong is one round, stopped and spawned again.

**The fixer's figure is bounded by the same data.** A fix sits inside the
cold-to-semi-cold gap — the fix, then the round that checks it — whose median
is 11.0 min and p90 21.1 min (n=84, same window and method as the table above),
so an hour is generous for the fix alone — though two of those 84 gaps did
exceed it. A fixer now also builds its own environment: the two demonstration
implementers on 2026-09-27 each installed the linting tools into a fresh venv in
about 42 seconds. The full dev requirements take longer and were not timed.

**The planner and implementer figures are not measured.** Before CF-563 this
session planned and implemented tickets itself, so no marker records how long
either takes. The implementer's 120 minutes is the ported harness's own figure;
the planner's 90 is a round's 60 plus the cross-check it spawns, which is a
second, smaller review. Both are the first rows to re-derive from the registry.

**These are the first figures, not the last.** The registry records the exact
duration of every round from now on; re-derive the table from it once there is
a run's worth, and say in the table where the new figures came from.

**If a claim has no registry line**, a compaction landed between the spawn and
the line. Time it from its `claimed:` comment instead, and before releasing it,
look for the subagent in the harness's list of agents this session spawned and
stop it if it is there.

#### The WIP limit and areas

**The WIP limit** (CF-563) is the `wip limit:` value in [This
run](START.md#this-run). It caps **targets in flight**, counted two ways:

- a **ticket** from its claim until its implementer reports — a PR, or a
  release
- a **PR** from the first round of its cycle until it reaches a terminal state

**Count from the registry and the claims this run made, never from a label
query** — the label-filtered listing [lags a fresh
label](#stale-claims), so a count taken just after a claim can come back one
short and let a slot be filled twice. A ticket released because its area was
held does not count; it is no longer in flight.

**The per-run PR cap still binds, and parallel work can overshoot it.** Before
dispatching an implementer, add the PRs this run has opened to the
implementers already in flight: if that reaches the [hard rules'](#hard-rules)
maximum, dispatch nothing. Checking only the PRs already opened lets three
implementers dispatched at five PRs open eight.

**Two dispatches are one target.** A ticket's planner and its implementer work
one after the other on the same claim, and a planner's own cross-check runs
inside its dispatch, so the limit counts targets, not agents. The number of
agents alive at once can briefly reach twice the limit.

**Areas are files, not labels.** A ticket may not start implementing while any
file its plan changes is held by other work. Two things hold files:

- a **ticket in flight**: the existing files its [planner](TICKETS.md#the-planners-brief)
  listed, as recorded in the log
- **this account's open PRs that are in cycle or `review-settled`**: every path
  the PR changes relative to `main`. A settled PR is about to land, and work in
  its files would collide with it. An
  `unsettled` PR releases its files: it may sit for weeks, and the collision is
  dealt with when it comes back

Other accounts' PRs hold nothing. The run cannot schedule around work it does
not control, and some of it — a long-lived mobile branch — would otherwise hold
half the tree.

**Read a PR's paths from git, not from `gh pr view`:**

```
git fetch -q origin main
git fetch -q origin "pull/<n>/head"
git diff --name-only --no-renames origin/main...FETCH_HEAD
```

**Two fetches, in that order, never one.** Fetching both refspecs at once writes
both into `FETCH_HEAD`, which then resolves to the first — `main` — and the
diff compares `main` with itself and prints nothing: a PR that appears to hold
no files. Found by running this block as first written, on 2026-09-27.

Two things the API's file list gets wrong for this purpose, both checked on
2026-09-27. It is the diff against the PR's *base*, so a PR stacked on another
PR's branch reports none of the files beneath it. And it lists a renamed file
by its new path only, so the old path is held by nobody. The
three-dot diff against `main` is what the PR will change when it lands, and
`--no-renames` lists a rename as a deletion and an addition, which holds both
paths.

**Two paths collide when they are the same, or when one is a directory that
contains the other** — in either direction. That is what makes the migration
rule work. A plan adding an Alembic revision lists `api/alembic/versions/` as
one path, because two plans that each take the next revision number collide
with no file in common; and **a PR that adds a revision holds that whole
directory too**, whatever its file is called, since its revision number is
taken the moment it merges.

**Why not the repo's labels, which look like areas.** Measured on 2026-09-27
over the 152 merged PRs, of which 99 close an issue: each of those takes the
labels on the issues in its `closingIssuesReferences`, counting only the ten
that read as areas — `api`, `web`, `devops`, `docs`, `eval`, `dead-time`,
`ball-detection`, `audio`, `scoring`, `mobile`. `api` PRs touched `web/src/` in
9 of 25, `web` PRs touched `api/app/` in 6 of 13, `devops` — the largest, at 42 —
touched 10 of the 11 top-level
directories any merged PR has touched, and `scoring`, `audio` and `mobile`
have never closed a merged PR. The files shared across the most of those ten
are exactly the ones two tickets would fight over: `.gitignore` under six,
`README.md` and `ARCHITECTURE.md` under five, `api/app/config.py` and
`render.yaml` under four. Label exclusivity would let two tickets that both edit
`config.py` run side by side, provided one was filed as `api` and the other as
`devops`. The labels are topics; nothing about filing a card makes them regions.

**What files cannot promise.** An implementer that has to change a file its plan
did not list may do so and must name it — see [its
brief](TICKETS.md#the-implementers-brief). Declared files make collisions rare,
not impossible. One that happens anyway usually surfaces as a PR GitHub cannot
merge — but not always: two migrations that each took the same revision number
merge cleanly one after the other and leave `main` with two Alembic heads,
which is why the directory rule above exists rather than trusting a merge
conflict to announce it.

#### Six agents, one credential

Every subagent posts as this run's account, so the whole run shares one GitHub
credential (CF-564). **The secondary limits are the ones concurrency reaches**,
not the hourly allowance: GitHub allows at most 80 content-generating requests a
minute and 500 an hour, and answers a breach with a 403 or 429, sometimes with a
`retry-after` header ([GitHub's REST rate-limit
documentation](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api),
read 2026-09-27). Every marker, review and claim comment creates content; label
changes are writes too, and are counted here as though they do.

Serial runs of this loop peaked at 25 comments in any sixty minutes and 16 in any
sixty seconds — every PR and issue comment from 2026-08-01 to 2026-09-27T00:00Z,
all authors, counted over a sliding window. Those 16 were `unsettled` comments,
each with a label change beside it, so that minute was nearer 32 requests than
16. Six agents at the hourly peak stay well inside 500. Six at the per-minute
peak — 96 comments before their label changes — do not stay inside 80.

**A secondary rate limit is not a usage limit.** The hard rule that stops the
loop on a usage limit is about the model's quota; this is GitHub's, and it
clears in minutes. The subagent that hits it stops, reports it with the
`retry-after` value if there was one, and posts nothing further. This session
writes its `finished:` line with the outcome `rate-limited` and dispatches
nothing until the `retry-after` has passed. With no `retry-after`, it waits a
minute, and doubles the wait each time the limit is hit again, as GitHub's
documentation asks. Then it routes the target again.

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
`date -u +%Y-%m-%dT%H:%M:%SZ` and write the **result**. This is one instance of
[Measure what you publish](#measure-what-you-publish); the general rule is
there, and it covers every number a run states, not only timestamps. Writing
the command itself into the log is not a near miss: everything downstream
compares strings, `$` sorts below every digit, so a literal `$(date …)` on that
line makes every comparison true and the per-run bounds silently become
all-time ones. Several bounds are recovered by comparing against this line
after a compaction — see [the counting
windows](#logging-and-the-counting-windows). Write it before the first
iteration does anything.

### Priority order

Finish work already in flight before starting anything new.

**Steps 1 and 2 are the two halves of one PR's cycle, not two sweeps over the
queue.** Read them as: pick a PR that needs a review, then carry *that* PR
through review and fix and re-review until it reaches a terminal state. Several
PRs can be in their cycles at once, up to [the WIP
limit](#the-wip-limit-and-areas), and each is carried through in the same way.
Running step 1 across every open PR and only then starting step 2 is still the
breadth-first pass ruled out below.

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

#### Order of work: every PR carried to a terminal state

**Take every PR you start all the way through.** Review it, fix it, check the
fix, settle or label it. Several can be in that cycle at once (CF-564), each
[claimed](#claims) for as long as it is. Do not run a pass over every open PR
and come back for a second lap.

**This was "one PR at a time" until CF-564, and the reason it was is still the
reason for everything here.** This loop gets interrupted: context is compacted
between iterations, a usage limit stops the run outright, and the operator
stops it, all at no point of your choosing. A breadth-first pass that is cut off
leaves every PR half-cycled, which is precisely the "abandoned mid-cycle looks
identical to reviewed clean" condition the terminal labels exist to prevent.
The answer used to be to keep the loop narrow enough that only one PR could
ever be mid-cycle, so that an interruption left everything else in a terminal
state — `review-settled`, `unsettled`, untouched, or reviewed-clean-but-held-back-
by-a-check, which [carries no label deliberately](FIX.md#the-cycle-and-the-settle-bar).

**The answer now is to make mid-cycle a state that can be read.** Every PR in
its cycle carries `in-progress`, so an interruption leaves the claim on exactly
the PRs it cut off, [the next run releases them](#stale-claims) and they are
cycled again from the top. The interruption is the same; what it leaves behind
is no longer ambiguous. That is what made running cycles side by side
defensible, and why the claim is not optional: **a PR in cycle without its claim
is the old failure, at up to six times the size.**

**What stays ruled out is starting cycles you will not carry.** Claim a PR only
when you can cycle it now — a free slot, and [budget reserved for
it](#the-run-budget). The state you carry is bounded by the limit rather than by
one: at most that many PRs' findings, with each PR's own markers holding the
rest.

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
Everything the paragraph below requires still happens: label each PR you are
holding, post each one's reason comment, and record them. A run that reads "ends" as
immediate leaves exactly the unlabelled-with-open-findings PR that paragraph
forbids.

If the budget runs out with findings open on a PR, it gets the same treatment as
the ceiling: `unsettled`, recorded, move on. Never leave a PR with open findings
carrying no label — unlabelled and unreviewed are indistinguishable to the next
run, which is the whole reason these labels exist.

**Reserve before you start, now that cycles overlap** (CF-564). Claim a PR for
a cycle only while the unspent budget covers three rounds for it *and* three for
every PR already in cycle; dispatch an implementer only under the same test,
counting the PR it will open. Three is what [one round of findings
costs](RATIONALE.md#what-a-night-costs) — cold, semi-cold on the fix, cold to
settle. Without the reservation, several cycles run the budget out together, and
every one of them lands on the paragraph above at the same moment: `unsettled`,
findings open, for arithmetic rather than for anything a reviewer found.

**When the reservation refuses, treat the budget as spent for starting
anything.** Finish the cycles already claimed — the reservation covers them —
and then follow the paragraph on a spent budget above: step 3 plans and files
only, and `review-only` ends. Without that, a run with one or two rounds left can
neither start work nor reach the rule that says what to do instead. **In `build`
count what is unspent against 35, not 40**, keeping the five rounds [reserved for
step 3](RATIONALE.md#what-a-night-costs).

**Both numbers were re-derived for concurrent cycles, and both stand.** The
ceiling is per PR: seven rounds bound one PR's cycle whether or not others run
beside it, so concurrency does not reach it. The budget of 40 is a cap on what a
night spends, not a throughput target: parallel cycles spend it sooner in
wall-clock time, but not more of it. What concurrency changes is *how* it runs
out, and that is what the reservation is for.

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

When log and markers disagree, **the markers win** — [Measure what you
publish](#measure-what-you-publish) is the general form of that. The log
records what a round intended; the markers record what the PR carries, and
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
label being removed. Four states carry a commits-since carve-out —
`review-settled`, and the `ran out of rounds`, `not our branch` and `head moved`
reasons for `unsettled` — and each re-opens the same way, so each gets the same
bound.
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

### Measure what you publish

**Anything that will be read as measured must come from the run that produced
it** — a number, but also a line reference, a file location, a quotation, a
grep result. Not from memory, not from a subagent's report, not from an earlier
run of the same command. The instances below are mostly numbers because numbers
are what a run publishes most; the head SHA on a marker and "the rule is in this
file" are the same claim wearing different clothes, and both have been wrong
here.

This is the general form of a rule the brief already carries four times, for
four surfaces: [Evidence](#evidence-and-the-higher-bar-for-rejecting-a-finding)
above, for the numbers in a reply that disputes a finding; the
[run-start timestamp](#log-before-you-finish-each-iteration), which must be the
output of `date -u` rather than the command written out; and
[the counting windows](#logging-and-the-counting-windows), which say the markers
beat the log. `REVIEW.md` carries a fourth, for the head SHA on a marker. Each
was written for its own surface, and each new surface then arrived as a fresh
mistake rather than as a case of something already known.

The run of 2026-09-02 published six wrong figures across three PRs while
reading this file every iteration (CF-370). They are listed because the shape
is easier to recognise than the rule is to remember:

- A **round count** acted on before it was measured — `unsettled: ran out of
  rounds` posted and labelled, then the marker query run, which said six of
  seven. A PR's terminal state was decided by a number held in the head.
- A **figure from a subagent's report** restated as measurement: "a bare
  `mypy api/app` reports 14 errors" when it is 93.
- A **grep result asserted for the wrong tree** — a PR body said the reference
  it added resolved in both directions on `main`, when one of the two
  directions *was* that PR's own change and returns nothing there.
- **Mutation rows printed as re-measured that were carried over.**
- A **lint count** of 2 that was 3, because the command was piped through
  `tail -2` and the summary line never read.
- **The same lint claim again three commits later**, with the count on screen
  directly above the commit — and one of the two commits in between is the one
  that wrote the corrective rule.

Three corollaries, each of which had to be learned separately:

- **Adding a test invalidates every previously measured failure count.** A
  mutation matrix is only true of the tree it was run against. After any test
  change, re-run the whole table — or print only the rows you re-ran, and say
  that is what they are. Reprinting old numbers under a heading that says they
  were re-measured is the case that happened, and the clearest one to avoid; a
  table mixing fresh rows with carried-over ones and saying so is honest, and
  one mixing them silently is the same defect wearing a smaller number.
- **Never truncate a gate's output past its summary line.** `| tail -2` hides
  the count on the line above it, which is how a regression becomes invisible
  to a run that believes it measured.
- **Write a gate's numbers from the run you just read, never from the run you
  expected.** This is [`BRIEFS.md`'s rule for
  mutations](BRIEFS.md#the-cold-reviewers-brief) — write the prose claiming a
  gap is closed *after* running it, never before — on a different surface. This is the one the two lint failures prove is load-bearing:
  the first came from not seeing the number, the second from seeing it and not
  letting it change a message already written. Composing the claim before
  reading the result is the actual defect, and no rule about *how* to run the
  command reaches it.

**One class of figure — the ones on `README.md` — is now checked mechanically
rather than promised.** The per-file token table, the five lap costs and the
select-versus-spawn difference stated below them are pure functions of the
brief's own file sizes, and `api/tests/test_overnight_brief.py` recomputes them
in CI (CF-371).

**Be exact about how little that covers.** It closes one shape — *right when
written, rotted untouched* — on one surface, the figures on `README.md`. The
documented instance is that table drifting by a third before CF-275 re-took it.
**None of the six failures listed above is on that surface**, so the check would
not have caught any of them. Nor are they one mechanism a second check could
close: a carried-over mutation row, a figure lifted from a subagent's report and
a grep run against the wrong tree are three different ways to publish a number
you did not measure. For everything above, the rule is still the whole of it.

**The tell is a sentence that would be embarrassing if someone re-ran it.**
Four of the six above were caught by a review round doing exactly that. The
other two the run caught itself, within minutes and before anyone looked — the
round count, three minutes after the label went on, and the second lint claim,
forty-two seconds after the commit. That is the encouraging half: the check is
cheap enough to run on yourself, and twice it was the run's own re-reading
rather than a reviewer that found the error.

### Repo traps that have already cost time

- Migration numbers collide. Check `api/alembic/versions/` for the current head;
  never assume.
- `api/tests/` exists and CI runs it. Test-only dependencies go in
  `api/requirements-dev.txt`, never `requirements.txt` — that file builds the
  production image.
- CF numbers have drifted from issue numbers. Check the highest existing `CF-`
  number; do not infer it from the issue count.
- **Editing any file under `docs/overnight/` invalidates `README.md`'s token
  table, and nothing tells you at edit time.** The table states each file's size
  to a tenth of a k and `README.md` states five lap figures derived from them;
  `api/tests/test_overnight_brief.py` asserts both against the files on disk. A
  paragraph added anywhere in the brief can move a row, and a paragraph added to
  `REVIEW.md` or `RULES.md` can move a lap figure too. So after touching a brief
  file:

  ```
  cd api && python -m pytest tests/test_overnight_brief.py
  ```

  **Re-measure rather than patching the row that failed.** These figures are
  asserted by **three separate tests** — the rows, the five lap figures, and the
  select-versus-spawn difference — and each names its own drift precisely, the
  lap one printing `the whole brief: page says 52k, files sum to 53k`. So the
  trap is not a silent failure, it is stopping after the first: fix the rows the
  first test named, re-run, and the next is still red. Adding the paragraph you
  are reading tripped all three in turn.
  Recompute all five lap figures in the same pass and state which are unchanged.
  Leave the across-the-split figures further down alone; they describe an older
  revision and the test's own message says so.

  **You will find out from a job called `API (ruff + mypy)`,** which is the part
  that costs the time: that job also runs `pytest api/tests/`, so the failure
  arrives under a name that sends you looking for a lint error that does not
  exist. Read which *step* failed before reading the diff. This has now cost
  **four** red runs across two docs-only PRs — three on #492 (`413bed0`,
  `971236c`, `1e2d108`) and one on #508 (`4042367`) — each caught by CI rather
  than at the keyboard.
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
