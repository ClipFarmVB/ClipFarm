---
name: prompt-writing
description: Principles for writing prompts, instructions, and any text that guides AI agent behavior — focused on outcomes, examples, strategies, and negative rules. ALWAYS use when writing such prompts, instructions, any text you think will be used in guiding AI agent behavior (especially skills).
---

> **In this repo there is nobody to ask.**
>
> This file's description fires on any text that guides an agent — "especially
> skills" — which is most of what the overnight loop edits, and
> `docs/overnight/README.md` defines that run as an agent with "nobody watching
> — overnight, or any stretch where questions cannot be answered". So "if the
> right placement isn't clear from context, ask the user" and "if the user
> explicitly signals weight" have no counterpart on those laps: the cost of
> asking is not small, it is the lap. Decide, write the decision and its
> reasoning where the next reader will meet it, and carry on — or file a card.
> `START.md` names the one condition that may stop a run, and an unclear
> placement is not it.
>
> Everything else here applies unchanged.



# Prompt Writing for AI Agents

This guide covers how to write persistent text that shapes agent behavior across many tasks. It applies to anything an agent reads repeatedly that influences its decisions: system prompts, agent definitions, skills, CLAUDE.md conventions, etc.

**For task-level instructions** — scoping specific assignments, delegating work, giving feedback on output — see the /task-guidance skill. That guide covers situational instructions written for one problem; this guide covers durable guidance that applies across many.

**This does NOT apply to informational documents** — code documentation, research material, cheat sheets, reference guides, and other knowledge artifacts have different goals (accuracy, completeness, discoverability) and their own guidance in /info-writing. This guide is specifically about text whose purpose is to **steer what an agent does**.

What's specialized here sits on top of that base rather than replacing it. A skill or CLAUDE.md is still prose someone reads: its sections still get ordered by what the reader needs, its names still have to resolve for a reader who wasn't there, and it suffers from the same filler. Load /info-writing alongside this one.

## Core Principles

### 1. Prompt for Outcomes

Focus on what needs to be achieved, not how to get there step by step.

Outcomes can be **concrete** (a goal to be achieved, a well described deliverable), **values-based** (what "good" looks like, what to prioritize), or **hard rules** (constraints that must always be followed). All are valid — the key is that you're describing the destination, not micromanaging the path. Hard rules are appropriate when the intent is to set non-negotiable boundaries — things the agent should never use judgment to override.

Sometimes the outcome genuinely includes a specific format or process — that's fine, state it directly. The problem is when you prescribe granular steps for things the agent can figure out on its own. Every instruction you add beyond what's needed dilutes the important ones and constrains judgment the agent could exercise better than your rules.

**Concrete outcome:**

```markdown
## Output Format

Produce a markdown table with columns: Company Name, Revenue, Employee Count, Location.
Sort by revenue descending. Include a summary row at the bottom.
```

This is specific because the format IS the outcome.

**Values-based outcome:**

```markdown
## File Organization

Prioritize locality and readability. Code that changes together should live together.
When adding new code, consider how a future reader will navigate and understand it.
```

This works because file organization is context-dependent — the agent needs to apply judgment.

**Overprompted (avoid):**

```markdown
## File Organization Rules

1. Put helper functions in the same file as the main function
2. Group related data in the same object
3. Place new functions near related functions
4. Keep files under 200 lines
5. Use index files for re-exports
```

This constrains decisions that vary by context. The agent may follow rule 4 and split a file that's more readable as one piece, or follow rule 1 when a helper is genuinely shared.

**The test:** For each piece of guidance, ask: _"Is this something the agent genuinely couldn't figure out from context and general capability?"_ If it could, you're constraining judgment, not adding value.

### 2. Use Examples to Illustrate

When an outcome is hard to convey in the abstract, a concrete example makes it tangible. Examples are powerful because they show what "good" looks like without having to exhaustively describe it.

Use examples to **frame the outcome** — to give the agent a mental model of what you're after. Do NOT use examples to prescribe what to do in specific situations.

**Good — illustrates a principle:**

```markdown
Trace the decision chain to find where things first went wrong.

For example, if an agent made a wrong API call, don't just note "wrong API" —
trace back: Did it not know the right API? Did it know but choose wrong?
Was there misleading context?
```

The example makes "trace the decision chain" concrete without prescribing every case.

**Bad — prescribes specific cases:**

```markdown
Always validate before acting. For example:

- Before calling the API, check the token
- Before writing a file, check permissions
- Before sending email, check the recipient
- Before deploying, check the tests
```

This anchors to specific patterns rather than teaching the principle. The agent will follow the list rather than applying judgment about what to validate when.

### 3. Suggest Strategies When Needed

When the path to an outcome is complex or has non-obvious optimizations, high-level strategies help. These are tips and approaches that a skilled practitioner would know — things that aren't obvious from the outcome alone.

Keep strategies as **suggestions**, not mandates. The agent should always be able to use its own judgment about when a strategy applies.

**Good — strategy as suggestion:**

```markdown
## Research Strategy

For broad topics, it often helps to start with a quick survey across multiple sources
before going deep on any one. This prevents anchoring to the first thing you find.

When sources conflict, trace each claim back to primary data when possible.
```

**Bad — strategy as mandate:**

```markdown
## Research Process

1. ALWAYS start by searching 5+ sources
2. Create a comparison table of findings
3. Resolve all conflicts before proceeding
4. Write a summary paragraph for each source
```

The mandated version forces a rigid process that may not fit every research task.

### 4. Recognize Information Hierarchy

When adding new content to an existing doc, think about how important the addition is relative to what's already there and to the doc's main point. If the user explicitly signals weight — and they often will ("put this at the top," "just a small note alongside the existing rule") — follow that. Absent an explicit signal, the act of asking in the moment isn't itself a weight cue; default to weighing the addition by the doc's context.

For each addition, ask:

- What is this doc primarily about?
- Does the new content support the main point, qualify it, or stand alongside it as a peer concern?
- Where would a reader landing cold expect to find this — at the top, inside an existing section, as a sub-note?

If the right placement isn't clear from context, ask the user. The cost of asking is small; the cost of an over-emphasized niche or an under-emphasized critical claim is large — it changes what readers walk away with.

Weight is one half of landing an addition well; reading as part of the doc around it is the other — the addition takes on the doc's vocabulary and framing, and supersedes rather than sits beside what it makes stale. (The original cited a `~/.claude/CLAUDE.md` section here. A cloud session
clones fresh and has no home-directory config, so the pointer is dropped rather
than left to dead-end — see this repo's `CLAUDE.md` for what it does carry.)

### 5. Use Negative Rules to Steer Away from Pitfalls

Being clear about what NOT to do helps agents avoid unproductive paths. Negative rules are especially valuable when a mistake is common, tempting, or hard to recover from.

Keep them concise. A short explanation of _why_ helps the agent generalize, but don't over-explain.

**Good negative rules:**

```markdown
- Don't add error handling for scenarios that can't happen — trust internal code and framework guarantees
- Don't create abstractions for one-time operations — three similar lines is better than a premature abstraction
- Don't commit .env files or credentials, even in examples
```

Each tells the agent what to avoid and gives enough context to understand why.

**Bad negative rules:**

```markdown
- Don't use var (use const or let instead)
- Don't use == (use === instead)
```

These are general knowledge any capable model already has. Adding them wastes context.

## Mindset and Approach

These postures complement the core principles above — carry them with you while writing, especially where the principles don't reach.

### Assume the Reader Is Smarter

Models get smarter every month. Our prompts should be written such that they'll age well into a world where they'll be read and followed by models that can figure out better and more efficient ways to accomplish our goals than we can now. The concepts most useful to smart models are principles, constraints, and outcomes - the things that form the context behind what it's doing and inform its reasoning

### Simplify

Stuffing in everything that might help is easy, and it costs more than it adds: every marginal piece of information raises the noise-to-signal ratio and dilutes the guidance that matters. Cut the fluff — the redundant, the obvious, what any capable model already knows — and keep what's defined nowhere else.
