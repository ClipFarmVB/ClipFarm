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

---

## This run

**Last updated: 2026-08-24.** If that date is not recent, stop and ask before
running.

### In scope

Work issues carrying the **`overnight-ok`** label:

```
gh issue list --state open --label overnight-ok --json number,title,labels
```

That label means a human has judged the ticket safe to implement unattended —
well-specified, no design or legal decision, no production data, no credentials.
It is the selection gate. **Do not take an issue that does not carry it**, however
appealing it looks; if you think one deserves it, argue for it in the report
instead of taking it.

Work highest priority first (`P0` > `P1` > `P2` > unlabelled). One ticket per
iteration. If a ticket turns out to need a decision after all, say so in the log,
drop it, and move on — do not guess.

If nothing carries the label, or everything that does is done, **stop the loop**.

### Environment notes for this run

- Render is **suspended** at $0/month. Starting it costs money.
- `claude-review.yml` is **disabled**, so nothing reviews PRs automatically.

---

## Standing policy

### First: establish what you can actually do

Before relying on any capability, check it, and record the result in the log in
one block. The first run discovered three gaps separately, mid-work.

- **Projects v2** — `gh project item-list 1 --owner ClipFarmVB --format json`.
  Needs the `project` token scope, which is often absent. This does **not** gate
  any work: selection is by label, which plain repo scope reads fine. It only
  affects board hygiene — without it you cannot remove the report issue from the
  project. Note the gap in the report and carry on.
- **Docker** — `docker info`. If absent, the local stack and the eval harness
  cannot run at all.
- **Gate tool versions** — read the versions `ci.yml` installs and compare with
  what is installed here. See the gate step below.

State every gap in the report. A capability you assumed and did not have is the
most expensive kind of surprise in an unattended run.

### Hard rules

- **Never** push to `main`, merge a PR, or force-push anything.
- **Only push to branches this run created.** Pushing to an existing PR's branch
  needs prior sign-off — the harness requires permission and the brief must not
  contradict it. If a fix belongs on someone else's branch, describe it in a
  review comment instead.
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
- **No attribution stamps.** Do not add "Generated with Claude Code", a
  `Co-Authored-By` trailer, a session link, or any similar footer to commits, PR
  bodies, review comments, or issues. Local settings suppress these but a sandbox
  does not inherit them, so this is on you.
- If a command fails because of usage limits, **stop the loop** — do not retry.
- If nothing in scope is actionable, **stop the loop**. A run that reviews two
  PRs and opens nothing is a fine outcome.

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
SINCE=$(grep -m1 '^run start: ' .claude/overnight-log.md | cut -d' ' -f3)
```

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

**1 — Review open PRs that need one.** A PR needs a review if it has **no
marker comment at all** — including one you opened earlier in this run — or if
it has commits since its most recent marker.

Read that as *marker*, not *review object*: every round posts with
`gh pr comment`, so a PR that has been reviewed a dozen times still has zero
reviews in GitHub's sense, and a test phrased against reviews reads every PR as
never reviewed.

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
that has since been replaced. Use the short SHA from
`gh api repos/ClipFarmVB/ClipFarm/pulls/<n> --jq .head.sha`.

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
- `reopened: <sha>` — not a round and not a verdict; it consumes no budget. It
  records that a settled or unsettled PR came back with new code, and it is what
  bounds the counting windows below. Next: a cold round.
- *no marker at all* — the query below prints nothing. The PR has never had a
  round. Next: a cold round, the first-look case.

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

Note what is deliberately *not* a marker: the `unsettled: …` comment that
records why that label went on. It is a label rationale, not a round — it
consumes no budget and routes nothing — and the pattern below excludes it by
matching only the two round kinds. Keep it that way if you add prefixes.

Every rule below that reads markers uses the same pattern. `gh`'s built-in
`--jq` takes a filter string only — it has no `--arg` — so the pattern is
interpolated by the shell and the filter's own quotes are escaped. Match it
**case-insensitively** (`; "i"`), so that a reviewer opening with `Cold:` does
not strand the PR:

```
MARKERS='^(cold|semi-cold|reopened):'
```

**Set it in every shell that uses it**, including the round-counting query
hundreds of lines below, which runs in a later iteration and possibly after a
compaction. It is a pattern to re-declare, not state that persists. An unset
`MARKERS` makes that filter `test("")`, which matches every comment on the PR
and returns exactly the count the marker scheme exists to avoid.

**Post them with `gh pr comment`, never `gh pr review`.** A review body does not
appear in `gh pr view --json comments` at all — #191 carries three comments and
one review, and that query returns only the three — so a verdict submitted as a
review strands the PR on whatever the previous comment said. This rule has to
travel to every subagent you spawn, and it is repeated in both briefs below for
that reason.

Read the latest marker, and route on it:

```
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"$MARKERS\"; \"i\")) | .body | split(\"\n\")[0] | sub(\"\r$\"; \"\")" | tail -1
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

**Count those `cold: clean` markers in the same window as the finding test
below.** Both halves of the two-clean rule have to agree on what "this PR" means
or the rule collapses: scoping the finding count to a re-open while counting
clean markers over the PR's whole life lets a re-opened `review-settled` PR —
which carries a `cold: clean` from before the new commits — settle after a
single clean round on code nothing has confirmed anything about. That is the
exact case the rule exists for.

It has its own pattern, and the same unset hazard as the others:

```
CLEAN='^cold: clean'
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"$CLEAN\"; \"i\")) | .id" | wc -l
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

**Set `FINDINGS` in the shell you ask from**, for the reason the `MARKERS`
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
them. When you apply it, open the same comment with one of three prefixes — that
prefix decides what re-opens the PR, and nothing else records it. Only one of
the three needs a human to clear it:

- *Ran out of rounds* (ceiling or budget). Commits since the label make it
  eligible again, round count reset — the same carve-out `review-settled` gets,
  and for the same reason: a PR that has since been fixed must not look like one
  nobody touched.
- `unsettled: not our branch` — the findings are fixable, but this run did not
  create the branch and may not push to it. **Commits since the label re-open
  it**, round count reset. A commit is exactly what resolves this one: the
  author reading the review and pushing a fix is the intended path, and it must
  not need a human to also clear a label by hand.
- `unsettled: needs a decision` — a finding requires a judgement nobody
  unattended should make. Only a human removing the label re-opens it. Commits
  do not, because the decision is not something a commit clears; the author
  pushing something unrelated would otherwise buy fresh rounds to re-derive the
  same finding off the same unchanged lines.

**When a carve-out re-opens a PR, take the label off and start cold.** The
routing table above reads the latest marker, and on a re-opened PR that marker
describes a head that no longer exists: a re-opened `review-settled` PR still
says `cold: clean`, which routes to "apply the label" that is already on it, and
a re-opened `unsettled` one still says `cold: findings`, which routes to fixing
findings raised against superseded code. Neither is what the PR needs. So:

- **Post a `reopened: <sha>` marker first, then remove the label.** In that
  order. The label is the only record that the PR was ever settled or unsettled,
  so taking it off without leaving anything behind destroys the boundary the
  counting windows depend on — a later iteration then counts clean and finding
  markers over the PR's whole life and settles it after a single clean round on
  new code, which is exactly what the two-clean rule exists to prevent. The
  marker is durable; the label was not. Left on, it also advertises
  `review-settled` to humans reading a PR with unreviewed commits.
- **The next round is cold**, whatever the last marker says. New code that
  nothing has looked at is the first-look case.
- **Re-read the round count, bounded by the `reopened:` marker you just
  posted.** Removing the label resets nothing on its own — no count lives in a
  label. Removing it makes the PR *eligible*; the marker *bounds the count*.
  Skip that read and the count still runs from `SINCE`, which includes every
  round already spent on this PR earlier tonight, and the ceiling arrives early
  on the PR just promised a fresh one.

**Every carve-out is the SHA test, not a timestamp.** "Commits since the label"
means: the PR's current head differs from the SHA in its latest marker. There is
no label event to read and no date to compare — the marker was written when the
label went on, so the two move together, and identity cannot be defeated by a
commit written before the review that pushed after it.

Compare the PR's current head SHA with the SHA in its latest marker. Different
means code has landed that no round has seen:

```
gh api repos/ClipFarmVB/ClipFarm/pulls/<n> --jq ".head.sha"
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.body | test(\"$MARKERS\"; \"i\")) | .body | split(\"\n\")[0] | sub(\"\r$\"; \"\")" | tail -1
```

**Compare SHAs, never dates.** A commit's `committer.date` is when it was
*written*, not when it was pushed: an author who committed on Tuesday and pushed
on Thursday produces a commit older than a Wednesday review, and a date test
concludes nothing has landed. That silently disables the
`unsettled: not our branch` carve-out, which is the mechanism this document
relies on for most of the queue — the case where the author's push is the only
thing that can continue the cycle. Identity has no such failure mode.

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
that is this run; on a branch this run may not push to, it is the author
responding to the review, and that case has to work or a
`unsettled: not our branch` PR could never satisfy the settle bar and would burn
to the ceiling on every visit. It gets the finding it is checking, the commits
that landed since the marker that raised it, and any reply on the thread — and
it is asked
to judge whether that finding is actually closed, then review the new head for
anything the change introduced. It is anchored by construction: it will check
the delta rather than re-derive the whole diff. That is the trade, and it buys
the one thing a cold round cannot do — someone other than the author confirming
the fix does what the finding asked.

**It posts one marker comment like every other round** — body starting with the
literal `semi-cold: closes` or `semi-cold: does not close`, before any heading,
then each finding it checked with the reason, then anything the fix introduced,
tiered as below. A same-line summary after the marker is welcome here too. Tell
it, as you tell the cold reviewer, to post with `gh pr comment` — never
`gh pr review`, never `/code-review --comment`. One comment per round, never one
per finding: the rules that recover state after a compaction count marker
comments, and a round posting several inflates that count as surely as one
posting none.

**A "does not close" verdict leaves the finding open.** Fix it again and take
another semi-cold round, or, if you cannot, label it `unsettled` with the
prefix that fits — `not our branch` or `needs a decision` — record it, and move
on. It does not become closed by being argued with.

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

Its brief: run `/code-review high` on that PR and post one marker comment — its body
**starting** with the literal text `cold: findings` or `cold: clean`, before any
heading or formatting, since that is what selection matches on. A one-line
summary after the marker on the same line is welcome (`cold: findings — 2
Critical, 1 Medium`); anything before it strands the PR. Then the findings, in
tiers:

- **Critical** — correctness, security, data loss
- **Medium** — should fix before merge
- **Nit** — style, naming, comments

**Post it with `gh pr comment`. Not `gh pr review`, and not
`/code-review --comment`.** This belongs in the brief you hand over, not only in
the selection rules above, because the skill you just told it to run documents
`--comment` as its own way to publish findings — inline review comments, which
is the move a reviewer holding the skill reaches for first. Both mechanisms are
invisible to `gh pr view --json comments`, which is the query that routes this
PR, so either one strands it on its previous marker.

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

Do not use `/code-review ultra`; it is a real level, but it is billed separately
and user-triggered, so an unattended run must not reach for it.

**2 — Address the review findings on the PR you are carrying**, if **this run**
created its branch. Not merely if the account matches: `gh api user -q .login`
also returns PRs this account opened on earlier nights, and pushing to those is
what the hard rule against pushing to branches this run did not create forbids.
For a PR you may not push to, describe the fix in a comment and label the PR
`unsettled: not our branch`. That is the cheap terminal state, not a dead end:
the author pushing a fix re-opens it on its own.

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

**When a cold round clears that bar, label the PR `review-settled`.** That is
the terminal state, and it is what stops future runs re-reviewing finished work.

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
has no such confirmation anywhere — one reviewer's silence would carry the whole
terminal state, and silence is the evidence this document rejects everywhere
else. Two independent cold passes is the weakest confirmation available when
there is nothing concrete to check, and it is the least that label should cost.

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
means depends on why you cannot fix it, and the two cases part company here:

- *A branch this run did not create.* You cannot push anything, so the work is
  writing the findings down where the author will act on them: **all** of them,
  including the ones that would have been a one-line fix, in the review comment
  and in a reply describing what you would have changed. Then label the PR
  `unsettled: not our branch`. The author's next push re-opens it.
- *A finding needing a human decision, on a branch you may push to.* First fix
  everything else that round raised and push it — those findings are real and
  abandoning them wastes the round that found them. *Then* label the PR
  `unsettled: needs a decision`.

Either way, record what is left in the log and the report, and move on. Do not
spend further rounds on it: a new round is cold to your reasoning but not to the
code, so it re-derives the same finding off the same unchanged lines.

**Always apply the prefix.** A bare `unsettled` says a PR is stuck without
saying what unsticks it, and the difference is whether the label clears itself
on the author's next commit or waits for a human who has not been told they are
needed.

**Ceiling: six rounds per PR per run, cold and semi-cold together**, so a
pathological PR cannot consume the whole night. Counting only cold rounds would
leave the semi-cold ones unbounded — every fix buys another check — and half of
a ceiling is not a ceiling. Six covers a PR with two rounds of findings and the
cold round that settles it. Hitting it is the same outcome: fix what you can,
label `unsettled: ran out of rounds`, record, move on.

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
[This run](#this-run) and no PR carries it.

**Run budget: 32 reviews per run**, cold and semi-cold together. Six rounds
across twenty PRs would permit far more — a whole night of nothing but
reviewing, which together with "stop on usage limits" means step 3 never
happens. **When the budget is spent, stop reviewing and go to step 3** — but
step 3 may then only plan and file, **not open PRs**, because a PR opened with
no review budget left is a draft this run cannot review, which the hard rules
forbid. Say so in the report. A spent budget clears steps 1 and 2 for the rest of the run;
without that fall-through the brief would forbid reviewing and gate ticket work
behind reviews that can no longer happen, and specify nothing to do next.

If the budget runs out with findings open on a PR, it gets the same treatment as
the ceiling: `unsettled`, recorded, move on. Never leave a PR with open findings
carrying no label — unlabelled and unreviewed are indistinguishable to the next
run, which is the whole reason these labels exist.

**Log every round as you finish it** — `PR #<n> — <cold|semi-cold>, round
<k>/6, budget <used>/32` plus the tiers found. Neither bound is enforceable
unless the count survives: context may be compacted mid-run, and counts you hold
in your head reset to zero when it is. Recover both from the log at the start of
every iteration, and cross-check the per-PR count by counting **marker
comments** — not comments, which also carry your step 2 fix replies and anything
a human wrote.

**Count only markers from this run.** Markers persist for the life of the PR;
the ceiling is six rounds *per run*, and an `unsettled: ran out of rounds` PR is
promised a reset when new commits land. A raw count undoes both — a PR that
spent six rounds last night would read as already at the ceiling before this run
touched it. So record the run's start time in the log's first line, and count
markers newer than it.

**When a PR was re-opened mid-run by new commits, count from the event for the
label that was removed — `unsettled` or `review-settled`, whichever it was —
instead, and only if that event falls inside this run.** Three states carry a
commits-since carve-out — `review-settled`, `unsettled: ran out of rounds` and
`unsettled: not our branch` — and each needs the bound; naming only one leaves a
mid-run re-opened PR counting from `SINCE` and hitting the ceiling early.
(`unsettled: needs a decision` has no carve-out, so it never needs this.) The
bound you want is the *later* of the two. A label applied last night is older than the
run start, so counting from it sweeps in markers this run has already spent, and
the ceiling arrives early on a PR that was just promised a reset.

`.created_at > "$SINCE"` is a lexicographic string compare against GitHub's
`2026-08-24T23:08:57Z`, so `SINCE` must be UTC with the `Z` suffix and nothing
else — which is what `date -u +%Y-%m-%dT%H:%M:%SZ` produces, and why the run
start is recorded in that form. An offset form like `2026-08-25T01:08:57+02:00`
sorts wrong against it and the count comes back low or zero — which reads as "no
rounds this run" and hands the PR a fresh six-round ceiling:

```
SINCE=$(grep -m1 '^run start: ' .claude/overnight-log.md | cut -d' ' -f3)
gh api --paginate repos/ClipFarmVB/ClipFarm/issues/<n>/comments --jq ".[] | select(.created_at > \"$SINCE\") | select(.body | test(\"$MARKERS\"; \"i\")) | .id" | wc -l
```

**What this costs, plainly — and it depends on who owns the branch.**

*PRs this run opened*, at most five, are the ones it can cycle. Clean on first
look costs two reviews; one round of findings costs three — cold, semi-cold on
the fix, cold to settle; two rounds costs five, which is most of the six-round
ceiling.

*PRs from earlier nights*, which is most of the queue, cost **one or two
reviews and then stop.** The run can review them but not push to them, so a
Critical or Medium ends in `unsettled: not our branch` after a single cold
round, and a clean one settles after two. The fix loop does not run on them at
all — the author's own push is what continues it, on a later night.

**The budget will bind on the first night, and that is expected.** Twenty PRs
are open, none carrying a marker, so a first pass over the queue alone costs
twenty to forty reviews — a clean one takes two, since it needs two clean cold
rounds to settle — before this run's own PRs are reviewed at all. Against 32
that does not fit, and it should not be written as though it does.

What follows from that: **the first night clears part of the queue and reports
the rest**, and later nights are cheap, because a PR that reached
`review-settled` or `unsettled` is skipped until its head SHA changes. The
budget is a bound on one night's spend, not a promise to cover the backlog.

The **ceiling**, by contrast, will rarely bind at all: it only applies to a PR
this run owns and keeps finding things in, since a PR from an earlier night
stops after one or two rounds regardless.

Reserve budget before opening anything: **do not open a new PR in step 3 unless
at least two reviews remain**, since a PR this run opens must be reviewable by
this run — a draft nobody has looked at is exactly what the hard rules forbid
leaving behind. If the budget cannot cover a review, step 3 writes the plan into
the log instead of opening a PR.

This is a deliberate narrowing, and it is the honest consequence of the hard
rule against pushing to branches this run did not create. The alternative is
sign-off to push to other people's branches, which is a decision for a human and
not something to assume unattended.

The semi-cold round is not defended on cost — it is defended on what silence
can and cannot establish. It asks a narrow question a reviewer can actually
answer: does this commit close this finding? Re-sampling the whole diff does not
answer that question however many times it is repeated, which is why fixes are
closed by a round pointed at them and settling still needs a cold one.

**Two knobs, and they are not interchangeable.** The **ceiling** (six rounds per
PR) exists for fairness: it stops one pathological PR eating a night that twenty
others are queued for. The **budget** (32 reviews per run) sets total depth
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
- Add to the `ClipFarm Backlog` project with the **Sprint field left unset** —
  these are for triage, not for silently joining the current sprint.
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
labelled `chore`, added to the project with the Sprint field unset. A log file in
a sandbox is a report nobody reads; the issue is the copy that arrives.

Put the same summary at the top of `.claude/overnight-log.md`.

The report contains:

- PRs reviewed, how many rounds each took and of which kind, and findings by
  tier
- PRs labelled `unsettled`, split by the three prefixes the label carries —
  `needs a decision` (only these want a human), `not our branch` (the author's
  next push re-opens it), and `ran out of rounds` (six-round ceiling or review
  budget) — and what is still outstanding on each
- Whether the review budget ran out, and which PRs never got a first round at
  all — with a deep queue this is the expected shape of a run, not a failure
- PRs opened, with card and branch — and **what to test to verify each one**
- Cards filed, one sentence each on why
- Tickets attempted but abandoned, and why
- Every decision needing a human call
- Anything that failed, **verbatim** — do not summarise errors away

Be honest. A report that overstates what landed is worse than a short one.
