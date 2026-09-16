---
name: task-guidance
description: How to give effective task instructions, delegate work, and provide feedback to AI agents working on specific problems. ALWAYS USE when delegating work, for example to subagents
---

> **In this repo, `docs/overnight/BRIEFS.md` wins for a review round of either
> kind, and there is nobody to ask.**
>
> Two carve-outs, because this file is loaded on every delegation and the
> overnight loop delegates every lap.
>
> **There is no user.** `docs/overnight/README.md` defines the run as an agent
> with "nobody watching — overnight, or any stretch where questions cannot be
> answered". So "raise it to me before running with it", the review gate
> ("wait for my go-ahead"), and anything else that waits on a person do not
> apply: the brief's own answer is to decide, record the decision and its
> reasoning, and carry on — or to file a card. `START.md` names the single
> case that may stop a run, and it is not this.
>
> **A review round is briefed by `BRIEFS.md`, not by this file's context
> advice — and that covers the semi-cold round too, not only the cold one.**
> A cold round is given the PR number and nothing about how the diff came to
> be; a semi-cold round is given the finding, the commits since, and nothing
> further. So three moves below are out of scope there, however good they are
> elsewhere: giving a summary alongside a pointer rather than the bare
> reference; offering a starting point, because "a concrete proposal transfers
> your thinking" is exactly what a cold round exists to prevent; and giving
> more context than you think necessary. Follow `BRIEFS.md` on WHAT a round is
> told; follow this file on HOW to say it.
>
> The rule being overridden is `BRIEFS.md`'s: a **cold** round gets the PR
> number and *"no plan, no reasoning, no summary of what was built, no earlier
> findings … Re-deriving that context is what a subagent normally costs you;
> here that cost is the point."*
>
> Added here rather than in `BRIEFS.md` because that file already owns the rule
> — `docs/overnight/README.md` says every rule lives in exactly one file, and
> this is a pointer to it, not a second copy.



# Task Guidance for AI Agents

This guide covers how to direct agent work on specific tasks — scoping assignments, transferring context, and iterating on output. It complements the /prompt-writing skill, which covers persistent behavioral guidance.

**The distinction:** Behavioral guidance defines how an agent _is_ — it's persistent, reusable, and applies across many situations. Task guidance tells an agent what to _do now_ — it's situational, written for one problem, and read once.

The writing approaches differ accordingly: behavioral guidance should be _restrained_ (every unnecessary rule constrains judgment across all future tasks), while task guidance should be _generous_ (context, rationale, and framing help the agent make better decisions in this specific situation, with no cost to future flexibility).

## Principles

### 1. Lead with Context, Then Task

Context is information the agent needs to make good decisions but can't infer from the task itself — the gap between what the agent can see (code, files, docs) and what it needs to know (why things are the way they are, where things are headed, what matters most). Providing it upfront prevents the agent from making locally reasonable decisions that conflict with the bigger picture. Always try to give more context than you think is necessary. Agents working on a problem should have a complete understanding of why they're solving what you've asked them to do and how it fits into the bigger picture.

```markdown
You'll be helping me research everything related to the CRM. Our CRM has a concept
of buyers and sellers, as we are an investment bank. Buyers are firms interested in
buying businesses, like PE firms. Sellers are businesses interested in selling.
Historically, we've kept buyer comms data in the buyer models, and seller comms data
in the seller models. However, we are currently migrating to get rid of all our seller
models and store everything in our buyer models instead. We already have all the seller
data published to our buyer models. So note that our next steps here are to remove all
reads to the seller models, then all writes, and then the models completely.

[... then the actual task ...]
```

The domain knowledge (what buyers/sellers are), the historical context (why there are two sets of models), and the direction of travel (migration plan and next steps) aren't things the agent could infer from code alone. Without this, the agent might make decisions that look correct locally but conflict with the migration — like improving a seller model that's about to be removed.

```markdown
Lets make some improvements to our agent teams setup. First, read everything under
/knowledge-store/core-knowledge/agent-teams for context.
```

Sometimes context is already written down — just point to it. When it is, **give the assignee a summary of what they'll find there and why it matters for their task**, not just a bare file path. A path alone forces the agent to read the entire file to figure out what's relevant. A summary + pointer lets them orient quickly and read with purpose.

```markdown
Read the buyer research guide at /knowledge-store/banking/buyer-research.md before
starting — it covers how we structure buyer profiles and what fields are required.
The "Qualification Criteria" section is most relevant to this task.
```

This is better than just saying "read buyer-research.md" because the agent knows what to look for and which section matters most. The more context files involved, the more this matters — without summaries, the agent spends its context window reading broadly instead of working.

Sometimes pointing isn't enough — when a critical convention lives in a doc the agent might not load, or when compliance matters more than authority, restate the key rule inline in the brief rather than just pointing. See /knowledge-restating for when restating earns the maintenance cost it imposes.

**For engineering tasks specifically**, an _engineering posture_ may already be written somewhere readers will look (a root README, a plan, a prior brief) — if so, point the agent at it so they inherit the right tradeoffs by default. See `/engineering-posture` for the axes (footing / horizon / feature audience / impact) and what each encodes. If no posture is recorded and the task is non-trivial engineering, name the posture inline in the brief itself; the brief can carry it for the task's lifetime without needing a durable home. For trivial tasks, just thinking through the posture before scoping is enough — no recording needed.

### 2. Define the Task by Defining Outcomes

Lead with the results you want to see. Describe what the finished product looks like — the deliverable, its shape, where it should live — before getting into how to get there.

```markdown
Lets set up a high level summary of all the database models that are involved, what
they represent, how they're connected with each other, and what they're used for in
the code. Leave out insights that are easy to see from looking directly at the
database schemas.
```

This tells the agent what the output is (a summary), what it covers (models, relationships, usage), where the bar is (high level, non-obvious insights only), and implicitly what to skip.

When scoping what to include, prefer qualitative judgment over arbitrary quotas. "Give me the most significant findings" lets the agent use judgment about what matters. "Give me the top 5 findings" forces a number regardless of whether 3 or 8 findings are actually significant. Quotas are useful for limiting volume (e.g., "keep it to a page" when you don't want a novel), but shouldn't be used to drive thoroughness — let the agent decide how much depth the topic warrants.

### 3. Trust the Agent with the How

Once outcomes are clear, the path to them often doesn't need to be prescribed. Skilled agents can chart a better course than any procedure you'd spell out — they'll notice shortcuts, recognize patterns you didn't, and adapt to what they find. Over-prescribing the method wastes that capability and sometimes forces the agent down a worse path than the one it would have picked on its own.

```markdown
Give me a categorized summary of how `buyerId` is used across the codebase — the
major patterns and any usages that stand out as unusual.
```

The agent decides how to find the usages, how to group them, and how to present the findings. None of that needs to be dictated in the prompt.

When the task genuinely requires a specific procedure — a sequence that must be followed, a convention with sharp edges, a workflow that can't be reinvented — prescribe it. The distinction is between methods the agent should be free to choose and procedures the task itself is built around. Default to describing the destination, not the route.

### 4. Provide Reasoning as Context

When you share the reasoning behind a decision, constraint, or direction, the agent gains context it can apply broadly — not just to the literal instruction. This is especially valuable for complex tasks where the agent will face judgment calls you can't anticipate.

```markdown
It will be a common pattern to stop all teammates and then respawn new ones later.
We should design our memory conventions to be resilient to this so that new teammates
have all the context they need and can continue where the old one left off. Of course,
don't mention this pattern in the memory file — this is for your context so you can
structure the wording better.
```

The reasoning (teammates get swapped out) explains why the memory conventions need to work a certain way. The agent can now make dozens of small wording decisions that all point in the right direction, without needing a rule for each one.

The more complex the task, the more reasoning matters. For a one-line fix, just say what to change. For a multi-file refactor or a new convention, the design intent is what keeps the agent's decisions consistent across the whole task.

### 5. Say What to Leave Out

Telling agents what's noise is as important as telling them what's signal. Without this, agents default to comprehensive coverage — which buries the valuable parts in obvious filler.

```markdown
Leave out insights that are easy to see from looking directly at the database schemas.
```

This one sentence prevents pages of obvious schema descriptions and focuses the agent on non-obvious relationships, gotchas, and context that a reader couldn't get from the schema alone.

## Strategies

These are situational techniques — useful in specific contexts, not something to apply to every task.

### Decompose the Problem into Dimensions

When a problem has multiple axes of concern, name them explicitly and describe what each cares about. This gives the agent a framework for decisions rather than a list of rules.

```markdown
We have a few dimensions here for naming/organization. Webhooks handle data from
different sources — these should be named to handle each source, but should delegate
writing to other functions. Helpers handle writes of data — these should be named to
specify exactly what structure is being written (eg. createCall if buyer,
createCloseCrmCall if seller), but they shouldn't care about the source. Backfill
jobs should be named with both target data structure and source in mind, if source
is significant.
```

Each dimension has its own naming concern. Without this decomposition, the agent would have to infer these distinctions from examples — and would likely apply the wrong dimension's logic to the wrong category.

This is especially valuable for refactoring, naming conventions, and organizational decisions where multiple concerns intersect.

### Use Examples to Show Generative Patterns

When you need the agent to extend a pattern to new cases, give examples that illustrate the pattern clearly enough to be generalized — not an exhaustive list to be followed.

```markdown
So strategically we should have: backfillEmailFromGmail, backfillZoom,
backfillCallsFromCloseCrm, backfillSmsFromCloseCrm, and backfillEmailFromCloseCrm.
```

Five examples, each showing how source and target map to the name. The agent can now name a new backfill job (say, backfillCallsFromOrum) without being told explicitly.

The distinction from [prescriptive examples](../prompt-writing/SKILL.md#2-use-examples-to-illustrate): prescriptive examples enumerate specific cases ("for API calls, check the token; for files, check permissions"). Generative examples show a pattern through cases so the agent can produce new ones.

### Offer a Starting Point, Anchored to the Goal

On an open-ended task, don't only leave the "how" open — a concrete proposal transfers your thinking and gives the agent a fast start. The move is to bind the proposal to the end-goal rather than to itself, and license divergence explicitly: name the goal as the invariant, offer your approach as disposable scaffolding, and tell the agent to discard it if it finds a better route to the same goal. Without the anchor, the agent tends to cargo-cult the suggestion even once it stops serving the goal.

```markdown
Defer to you on exactly how to structure this — the end goal is just to have a
deterministic system. Adjust the approach as needed; I'm just providing a starting point.
```

This differs from [Trust the Agent with the How](#3-trust-the-agent-with-the-how): there you stay silent on the method; here you *supply* one to accelerate the agent, while keeping the goal — not the method — authoritative.

### Set the Autonomy Bar

On a large task, the agent will hit decisions the brief never settled. Tell it where its authority ends: decide the low-impact calls on its own, and raise the high-impact ones — those touching critical data, core design, or anything costly to reverse — rather than guessing. This earns its place when the task is big enough that undecided calls are inevitable and some of them matter; on a small or fully-specified task it's noise.

```markdown
Use your judgement on the smaller calls, but anything that changes the data model or
how developments get written, raise it to me before running with it.
```

The complement to this is a review gate — "wait for my go-ahead before implementing." That's situational too: reach for it when you know more feedback is likely coming, not as a default close on every brief.

### Restate Key Instructions at the End

In a long brief, the constraints that matter most get buried in the middle. Open with context and the task, work through the detail, then close by restating the handful of instructions that most shape the outcome. The repetition isn't redundant — it fixes the critical points in the last, most-attended position so they don't get lost in the wall of detail. Skip it for short briefs, where nothing is far enough from the end to be forgotten.

## Reminder When Spawning New Teammates / Subagents

Teammates and subagents spawn into your working directory when you invoke the tool to create them. Make sure this is your original working directory, as if they spawn somewhere else they may lose access to skills.

## Iterating on Output

### Give Structured Inline Feedback

When reviewing agent output, reference the agent's own structure and give targeted corrections rather than rewriting the prompt.

```markdown
1. Yes lets look into making a helper for this
2. This is fine
3. What's the impact of this?
4. This is fine, as the buyer backfills write both seller and buyer data to the
   buyer models.
5. These are good. Whether these functions are placed in the right spot is a
   different question.
6. This is actually fine — we just need to clarify what writes to buyer models and
   what writes to seller models.
```

This builds on the agent's existing structure, clearly separates "fine" from "needs work" from "need more info," and adds new context (point 4's explanation) right where it's relevant.

### Add Context Through Corrections

Feedback is an opportunity to transfer context the agent was missing. When correcting, include the reasoning — this prevents the same class of mistake on the next iteration.

```markdown
fetchAndCheckMetadata shouldn't be responsible for checking the db for the id — this
belongs in whoever is calling it. It is right to return the rfc5322MessageId though.
```

This doesn't just say "move the db check" — it states the design principle (responsibility boundary) so the agent applies it consistently to similar decisions.

### Prioritize Explicitly

When giving feedback that spans multiple concerns, state priority order. Agents will otherwise treat all feedback as equal weight.

```markdown
Lets prioritize the efficiency improvements first, while keeping in mind the rest
of the concerns.
```

The same move applies up front, for the whole task, not just when reacting to output: when several concerns compete, name a ranked value hierarchy that governs every tradeoff the agent will make. This gives the agent a decision rule for the judgment calls you can't enumerate — it knows which way to lean when two of your goals pull against each other.

```markdown
In order of priority: we value correctness first — we must keep writing the correct
data. We value covering all paths next. We value cleanliness after that.
```
