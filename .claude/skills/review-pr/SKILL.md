---
name: review-pr
description: Review a pull request with findings tiered critical / medium / nit, design challenges where warranted, and a plain merge verdict. Use when the user asks to review a PR, asks whether a PR is good to merge, or names a PR number and wants it looked at.
---

# Review a PR

Run a tiered review of a pull request and answer the merge question outright.

## What to do

Invoke the `code-review` skill against the PR the user named:

```
/code-review PR #<n> with flags on critical, medium, and nit tiers and challenges on design where appropriate. Is it good to merge?
```

If the user gave no PR number, ask for one — do not review the working tree
instead, which is a different question and a different answer.

## What the answer has to contain

**Tier every finding**, and let the tiers mean what they say:

- **Critical** — wrong behaviour, data loss, a security hole, or a claim in the
  diff that is false. Blocks merge.
- **Medium** — a real defect that will cost someone later: a missed case, a test
  that passes for the wrong reason, an interface that invites misuse. Blocks
  merge unless the author says why not.
- **Nit** — style, naming, wording. Never blocks merge.

**Anchor every finding to `file:line`** and quote the line. A finding without a
location is not yet a finding.

**Challenge the design where it deserves it**, separately from the findings —
whether the change solves the right problem, whether a simpler shape exists,
whether it contradicts something already in the repo. A design challenge is not
a Critical; keep them apart so the author can act on the blocking set first.

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
has produced a false finding here more than once. If a mutation turns many tests
red, that is the tell you broke something other than what you meant to.

**Quote only what you fetched in this round.** Line numbers, SHAs, counts and
timestamps go stale between rounds, and a recalled number presented as evidence
is how a correct finding gets waved away.

## On re-review

When the user says changes were made, **read the new head before responding**.
If the head SHA has not moved, say so plainly and show that the findings still
reproduce, rather than accepting or disputing the claim.
