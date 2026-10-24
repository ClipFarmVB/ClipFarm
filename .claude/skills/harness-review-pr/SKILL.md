---
name: harness-review-pr
description: (Repo-pinned copy of the harness plugin's review-pr skill, for sessions without the plugin; prefer harness:review-pr where the plugin is installed.) Review a pull request by hand, with findings tiered critical / medium / nit, design challenges where warranted, and a plain merge verdict. Use when the user asks to review a PR, asks whether a PR is good to merge, or names a PR number and wants it looked at.
---

# Review a PR

Run a tiered review of a pull request and answer the merge question outright.
This is a review for the human in front of you. A harness loop's review rounds
are a different mechanism with their own brief.

## What to do

Invoke the `code-review` skill against the PR the user named:

```
/code-review high PR #<n> with flags on critical, medium, and nit tiers and challenges on design where appropriate. Is it good to merge?
```

If the user gave no PR number, ask for one — do not review the working tree
instead, which is a different question and a different answer.

**Post nothing to the PR unless the user asks.** If they do, and the repo runs
a harness loop, never begin a comment with one of the loop's machine prefixes
(the repo's `CLAUDE.md` or harness profile lists them). The loop routes on
those, and a hand review wearing one would move a PR through its cycle. A
comment without a prefix on a loop PR is read by the loop as the human's
request, and it will act on it. Say that before posting.

## What the answer has to contain

**Tier every finding**, and let the tiers mean what they say:

- **Critical** — wrong behaviour, data loss, a security hole, or a claim in the
  diff that is false. Blocks merge.
- **Medium** — a real defect that will cost someone later: a missed case, a test
  that passes for the wrong reason, an acceptance criterion not met, an
  interface that invites misuse. Blocks merge unless the author says why not.
- **Nit** — style, naming, wording. Never blocks merge.

**Anchor every finding to `file:line`** and quote the line. A finding without a
location is not yet a finding.

**Review anything visible from screenshots**, using the `ui-craft` skill's
rubric. Run the app and capture the screens the diff touches.

**Challenge the design where it deserves it**, separately from the findings —
whether the change solves the right problem, whether a simpler shape exists,
whether it contradicts something already in the repo (its spec, its
architecture docs). A design challenge is not a Critical; keep them apart so
the author can act on the blocking set first.

**End with the merge verdict in one line** — good to merge, or not, and what
would change it. That is the question actually being asked, and a review that
lists findings without answering it makes the reader do the arithmetic.

## Verify before reporting

**Check each finding against the code as it is on the PR head**, not as you
remember it from an earlier round. A finding that does not reproduce is
withdrawn, not softened.

Where a finding says a test is inadequate, **mutate the code it covers and show
the test still passes**. Run the mutation carefully — a mutation that fails for
an unrelated reason (a `NameError`, a wrong parameter name) proves nothing and
has produced false findings before. If a mutation turns many tests red, that is
the tell you broke something other than what you meant to.

**Quote only what you fetched in this round.** Line numbers, SHAs, counts and
timestamps go stale between rounds, and a recalled number presented as evidence
is how a correct finding gets waved away.

## On re-review

When the user says changes were made, **read the new head before responding**.
If the head SHA has not moved, say so plainly and show that the findings still
reproduce, rather than accepting or disputing the claim.
