# Step 1 — running a round

Read on a lap that **reviews a PR**: which PRs need a round, the marker scheme, routing, posting, reading state back, the terminal labels, and the briefs handed to the cold and semi-cold reviewers.

Part of the unattended-run brief — see [`README.md`](./README.md).

| file | when to read it |
|---|---|
| [`START.md`](./START.md) | once, at the start of a run |
| [`RULES.md`](./RULES.md) | **every iteration** |
| [`REVIEW.md`](./REVIEW.md) | a lap that reviews a PR |
| [`FIX.md`](./FIX.md) | a lap that fixes findings |
| [`TICKETS.md`](./TICKETS.md) | a lap that implements a ticket |
| [`REPORTING.md`](./REPORTING.md) | end of the run |
| [`RATIONALE.md`](./RATIONALE.md) | optional background |

---

#### Step 1 — which PRs need a round

**1 — Review open PRs that need one.** One test, not two:

> **A PR needs a round unless it carries `review-settled` or `unsettled` and
> that label's carve-out has not fired.**

**And one filter in front of that test: the run's `review scope`.** With scope
`own`, a PR whose author is not this account is out of scope and gets no round —
it is not skipped-because-settled, it is not in the queue at all. With scope
`all`, every open PR is in the queue.

The value comes from the `review scope:` line in [This run](START.md#this-run) —
**read it there, and if it is missing or holds a value you do not recognise,
stop and ask** rather than assuming `own`. This is the enforcement point, so a
run reaching it without having re-read that block would otherwise default
silently; see [Scope](START.md#scope-whose-prs-get-reviewed).

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

Note that scope and pushability are now the **same** test, not merely a shared
first half: both ask whether the PR's `.user.login` is this account, and since
2026-08-29 pushing adds nothing further — see
[The push test](RULES.md#the-push-test). So a PR in scope at `own` is one this run
may push to, full stop, unless the harness itself refuses. They were wholly
different questions while the push rule keyed on which run created the branch,
and differed by one condition while it also read commit authorship.

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

##### What a non-`gh` tool must provide

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
   [Scope](START.md#scope-whose-prs-get-reviewed) says takes a human setting it for that
   run. A run that grants itself that permission because it could not read its
   own login has escalated its scope to work around a missing capability, and it
   would do so on the *common* path, since a run without `gh` is the expected
   case rather than the unusual one. [Hard rules](RULES.md#hard-rules) already covers
   this: if nothing in scope is actionable, stop.

   Confirm you get a login and not a null before trusting the filter. A `null`
   under `own` excludes every PR silently, and the run reports a full queue as a
   deliberate scoping decision having reviewed nothing.

**Confirm your tool paginates before you trust a marker read**, and name the
tool in the report. A run that cannot establish point 1 should say so and treat
every marker read as unverified rather than assuming it saw the newest.

**Where the GitHub MCP tools fail these, specifically.** They are the expected
non-`gh` tool in the web sandbox, and they satisfy the list — but three of the
failures are silent, and all three were hit in the run of 2026-08-27:

- **Labels cannot be read off a PR the obvious way.** `issue_read` with
  `get_labels` returns `Could not resolve to an Issue` for a pull request
  number. Read them from `list_pull_requests` instead. Use that to *verify a
  label landed*, too: the write returns success whether or not it did, so a
  settle that never took looks identical to one that did.
- **Writing labels replaces the whole set.** Read the current labels first or
  you will silently drop one. On a PR carrying only your own label this is
  invisible.
- **The list calls can exceed the context budget outright.** `list_issues` and
  `list_pull_requests` return full issue and PR bodies even with
  `minimal_output: true`, and on this repository a default page is refused for
  size — which costs a call and returns nothing. Pass a small `perPage` (1–5)
  and page. This is why point 1's pagination requirement is not academic here.

None of these changes a rule. They change what "I checked" is worth, which is
the same class of problem as everything else in this section.

##### Markers: what a round writes

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

##### Routing: what the marker tells the run to do next

Routing reads the latest marker and compares its SHA with the PR's current
head. Those two pick the row. The `cold: clean` rows at a matching SHA need one
more fact each, named in the third column and drawn from three sources —
whether a finding is still open, whether one was raised in the counting window,
and how many head-matching `cold: clean` markers the PR carries. Read each from
the list under the table; do not count the rows or the sources against this
sentence, for the reason [the numbered list
above](#what-a-non-gh-tool-must-provide) gives:

| latest round marker | SHA vs head | further condition | next |
|---|---|---|---|
| *none* | — | — | **cold round** — the first-look case |
| `cold: findings` | matches | — | **step 2** — the findings stand against this code, fix them |
| `cold: findings` | differs | — | **semi-cold** — a fix has already been pushed; check those findings against the new head |
| `cold: clean` | matches | a finding is still open, **and** commits have landed since the marker that raised it | **semi-cold** — that fix is what needs checking; only a semi-cold can close a finding |
| `cold: clean` | matches | a finding is still open, **and** nothing has landed since the marker that raised it | **step 2** — nothing to check, so spend no round; fix it |
| `cold: clean` | matches | nothing open, and a finding was raised **in the counting window** | **settle it** — apply `review-settled` |
| `cold: clean` | matches | nothing open, none raised in the window, and this is its **only** head-matching `cold: clean` | **cold round** — the second of the two that case requires |
| `cold: clean` | matches | nothing open, none raised in the window, and a **second** head-matching `cold: clean` is already there | **settle it** — apply `review-settled` |
| `cold: clean` | differs | — | **cold round** — new code nothing has looked at; see the re-open rule below |
| `semi-cold: closes` | — | — | **cold round** |
| `semi-cold: does not close` | matches | — | **step 2** — fix it again |
| `semi-cold: does not close` | differs | — | **semi-cold** — another fix has landed since; check it |

Why several of those rows exist, since each was added to close a specific way
the run could get stuck:

- **The two `differs` rows for findings** — `cold: findings` and
  `semi-cold: does not close` — exist because a marker alone cannot express "a
  fix has already been pushed". Without them a compaction mid-fix routes the run
  back to step 2 forever, and the settle bar is never reachable.
- **`semi-cold: closes` requires two things**, not one: the fix closes the
  finding it claimed to, **and** the round raised nothing new above a nit. A
  round that introduced a Critical or Medium of its own writes
  `does not close`, because both are open findings and both route the same way.
- **`semi-cold: does not close` at a matching SHA cannot be rescued by a cold
  round.** The settle bar requires a semi-cold check to close a finding, and a
  cold round posted on top would make its own marker the latest and hide the
  open finding from this very rule.
- **The two open-finding rows are what this table adds rather than restates.**
  The state is reachable — a cold round posted over an unclosed finding leaves
  exactly this — and the bullets this table replaced routed it nowhere. They
  split on whether there is anything to check. Where a fix has landed since the
  finding's marker, a semi-cold round checks it, because only a semi-cold can
  close a finding: sending that to step 2 instead does not terminate — the fix
  moves the head, the `differs` row calls a cold round, a clean cold round lands
  back here with the finding still unclosed, and the PR burns to its ceiling on
  code that was already fixed. Where nothing has landed, the verdict is settled
  before a reviewer could read anything, so **spawn no round**: go straight to
  step 2, spend nothing against the ceiling or the budget, and let the fix create
  something for the next round to check.
- **"Still open" is read from the marker stream, not from memory.** A finding is
  open when the PR carries a `cold: findings` or `semi-cold: does not close`
  marker with no later `semi-cold: closes`. That is the test routing uses — a
  proxy, with the settle bar still the authority, since a `semi-cold: closes`
  checking one finding says nothing about a second raised by the same round. It
  survives a compaction, and identifies *the marker that raised the finding*
  — the latest such marker with no `closes` after it. Do not substitute a count
  of findings *raised*: the `FINDINGS` query below never returns to zero once a
  PR has had one, so routing on it would hold a converged PR on these rows until
  the ceiling stopped it.
- **"In the counting window" is not "ever".** On a PR that has been re-opened the
  window starts at its latest `reopened:` marker, exactly as
  [the finding count](#reading-state-back-queries-labels-counts-and-windows)
  specifies. A PR re-opened by a push, carrying a finding closed before the
  re-open and none since, is the never-had-a-finding case for settling and needs
  its two clean rounds. Settling it on one because it once had a finding fails
  toward less review, which is the direction this document guards hardest.
- **A clean round is not by itself permission to label.** Most rows starting from
  `cold: clean` do not settle. The full bar, including what "nothing open" means
  across rounds, is the [settle bar](FIX.md#the-cycle-and-the-settle-bar) — the table
  routes on it, it does not replace it.

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

**Every block that uses it re-declares it, and that repetition is deliberate —
do not factor it out.** The blocks below are meant to be run as they stand, and
an agent landing mid-document copies one block, not the section around it. With
`ROUNDS` unset the filter becomes `test("")`, which matches **every** comment on
the PR (verified against `gh`'s own jq: 4 of 4 strings, against 1 of 4 for the
real pattern). In the round-counting block that makes the count come back as the
PR's total number of comments, which reads as a spent ceiling and labels
`ran out of rounds` a PR that has spent none — a fail-open in the one direction
this document guards hardest. The
same hazard is stated for `FINDINGS` below, for the same reason.

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
compaction. It is a pattern to re-declare, not state that persists. What an
unset `ROUNDS` does, and why every block here declares it, is stated with the
pattern itself above.

##### Posting a round: comment and review

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

##### Reading state back: queries, labels, counts and windows

Read the latest marker, and route on it:

```
ROUNDS='^(cold: (findings|clean)|semi-cold: (closes|does not close)) @ ?[0-9a-f]{7}'
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
and needs the round that marker names — including `cold: clean`, which [the
routing table](#routing-what-the-marker-tells-the-run-to-do-next) gives several
next actions depending on what else is true of the PR. The gap between a
settling round and the label it earns is a window like any other: no commit, no
label, and a run can die in it. Reading `cold: clean` as terminal is what
strands such a PR, and it strands the never-had-a-finding case twice over —
once between its two clean rounds, once after the second.

A `cold: clean` marker on an unlabelled PR therefore means: apply the label now
— settle bar permitting — unless one of the routing table's exceptions applies.
Two do: a PR with a finding still open takes a semi-cold round, or step 2 where
nothing has landed since the finding's marker, rather than settling; and a PR
with no finding raised in the counting window that carries only one such marker
wants its second clean cold round first.

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

##### The terminal labels and their reasons

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
- `unsettled: latched @ <sha>`

The four differ in what clears them, and that is the property to check before
choosing one — a reason that clears itself on a commit is the wrong reason for
something a commit does not fix:

| reason | what it records | what re-opens it | round count |
|---|---|---|---|
| `ran out of rounds` | the ceiling or the budget stopped it, findings still open | **new commits**, no human needed | reset |
| `not our branch` | findings are fixable, but the branch belongs to **another account** and this run may not push | **new commits**, no human needed | reset |
| `needs a decision` | a finding needs a judgement nobody unattended should make | **a human removing the label** — commits do not | — |
| `latched` | this account's PR and [the push test](RULES.md#the-push-test) passes, but the **harness** refuses the push | **a human, outside the loop** — no run can clear it | — |

**When more than one is true, `needs a decision` wins**, over each of the other
three; note the losing one in the comment as context rather than as the reason.
Among the rest, `latched` beats `ran out of rounds`, and `not our branch` beats
`ran out of rounds` too — though where **those** two coincide the choice is
cosmetic, since both clear on a commit and both reset the count, and `not our
branch` wins only because it says why this run could not have fixed the PR at
any budget. `not our branch` and `latched` cannot both apply: the first is only
ever another account's PR, the second only ever this account's.

Why that order and not another: the two commit-cleared reasons discharge
themselves on the author's next push, so a PR that also needs a judgement would
have that question dissolved by an unrelated commit and the human never asked.
The reason that needs a human therefore has to win, or it is not a reason at
all. The pairings involving `needs a decision` are worked through case by case
in [choosing a reason](FIX.md#when-you-cannot-fix-it-choosing-a-reason); the two that
do not involve it are stated here, and repeated only as a pointer in the
tie-break note there.

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
the rule against leaving open findings unlabelled — stated above, and again
with the budget — still demands one.

Each reason in full — what it means, and why it clears the way the table says:

- `ran out of rounds` — the ceiling or the budget stopped it. New commits
  re-open it, round count reset: a PR that has since been fixed must not look
  like one nobody touched.
- `not our branch` — the findings are fixable, but the branch belongs to
  **another account**, so this run may not push to it. **New commits re-open
  it**, round count reset. A commit is exactly what resolves this one: the author
  reading the review and pushing a fix is the intended path, and it must not need
  a human to also clear a label by hand.

  **This reason is only ever about another account's PR**, and those reach the
  queue only at `review scope: all`.
- `needs a decision` — a finding requires a judgement nobody unattended should
  make. Only a human removing the label re-opens it. Commits do not, because the
  decision is not something a commit clears; the author pushing something
  unrelated would otherwise buy fresh rounds to re-derive the same finding off
  the same unchanged lines.
- `latched` — this account opened the PR and [The push test](RULES.md#the-push-test)
  passes, but the push does not go out: the harness refuses it, or the remote
  rejects it for a reason no run can resolve. The findings may be entirely
  mechanical; what is blocked is the mechanism, not any one of them.

  **Since 2026-08-29 this reason no longer covers a collaborator's commits.** The
  push test stopped reading commit authorship, because authorship records where
  code came from rather than who is holding the branch, and rebases and replays
  decouple the two — see [the push test](RULES.md#the-push-test) for the
  measurement that retired it. A branch a teammate has pushed to is now
  reviewable *and* pushable by the letter of this document, so **the judgement
  moved to the run**: read the PR, and where a conversation about work in
  progress is live, write a comment instead of pushing. That is discretion, not a
  gate — say in the report when you exercised it.

  **Commits do not re-open it, and that is the whole point.** At `own` the
  account that pushes is the one running, so a commits carve-out would re-open
  the PR on each of its own pushes, burn a cold round re-deriving findings it
  still may not fix, and re-park — forever. The carve-out's usual justification
  (*the author reading the review and pushing a fix is the intended path*)
  assumes the author is somebody else. Here the author is this account, and it is
  the one actor that cannot act.

  **It is cleared by a human, outside the loop, and there is deliberately no way
  for a run to clear it.** Merge the PR, push the fix yourself, or fix whatever
  is refusing the push — whichever fits. What there is *not* is a comment or a
  label a run can act on to authorise itself past a gate that stopped it.

  That was tried. A `latch-override:` grant was added on 2026-08-25 and removed
  on 2026-08-26, and in between it produced the worst defect in the change that
  introduced it: the run could post its own override and clear its own latch.
  Closing that took an author filter, a hard rule, a verdict match, a SHA
  validity check, an identity-based freshness test and a four-branch parser —
  and successive reviews kept finding more, including a query that never
  executed at all. It was the only thing here that *granted* a permission
  rather than withholding one, and that asymmetry is what made it hard to get
  right. Removed under CF-274.

  **The cost is real and intended.** A latched PR cannot be finished by the loop.
  Report it by name so a human picks it up — that is the only route out, and the
  report is the only place it is visible.

  **The latch is permanent by design, and since the grant went there is nothing
  to soften it.** A refused push is not a condition a run can retry its way out
  of: whatever refused it will refuse the next one too, so re-attempting only
  spends budget re-deriving findings that still cannot be pushed. The cost of the
  permanence lands on the report line rather than on a mechanism.

  **It should now be rare.** Until 2026-08-29 the commonest way to latch was a
  collaborator's commit on the branch, and on one measured queue that fired on
  ten PRs of eleven, every one of them a false positive. With authorship out of
  the push test, a latch means the push machinery itself said no — which is worth
  reporting loudly, because it is closer to a broken environment than to a
  routine outcome.

##### Record comments, human removal, and re-opening

**Applying either terminal label posts a record comment**, and that is what
makes a human's removal detectable at all:

- `unsettled` → `unsettled: <reason> @ <sha>`
- `review-settled` → `settled: @ <sha>`

**A human clearing a label leaves no `reopened:` marker**, because nothing this
run did re-opened it. So when you pick up a PR carrying one of those record
comments but *not* its label — a maintainer removed it — **write the
`reopened: <sha>` marker yourself before the first round**, then let the routing
table pick the round, exactly as for a carve-out re-open.

Do not force a cold one: a maintainer who clears `unsettled: not our branch`
*after* the author pushed a fix leaves a PR whose last round is `cold: findings`
at a stale SHA, which wants a semi-cold check. Forcing cold there cannot close
the finding, so the settle bar stays unreachable and the PR burns to the ceiling.

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

**`unsettled: latched` is the exception to the two rules above** — the
`reopened:` marker and the routing that follows it. It still posts its record
comment like every other reason; what it does not get is a `reopened:` marker or
a route back into the cycle. Nothing about a latch is cleared by removing a
label: whatever refused the push will refuse the next one, so the PR latches
again on the next round. **Retry the push once** before re-applying — a refusal
can be transient in a way a collaborator's commits never were, and that is the
one respect in which this reason got cheaper on 2026-08-29. If it is refused
again, re-apply `unsettled: latched @ <sha>`.

**Say in the report that the label was removed while the branch was still
latched**, and say it every time it happens. Removing the label is the only
thing a maintainer *can* try — there is no override to post any more — so
without that line the loop re-applies it nightly, forever, with no explanation
reaching the person undoing it. That is the cost of having no in-loop route out,
and the report is the only place it is payable. Nothing unsafe follows either
way: the push test runs before every push.

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

##### Cold and semi-cold rounds

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
##### The semi-cold reviewer's brief


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
[the routing table](#routing-what-the-marker-tells-the-run-to-do-next) gives
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

##### The cold reviewer's brief

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
