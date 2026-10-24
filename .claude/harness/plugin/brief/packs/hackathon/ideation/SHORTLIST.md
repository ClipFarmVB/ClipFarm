# Shortlist: one-pagers, the packet, the handoff

Read on the **last laps of a run**: deepening the shortlist, running the head
judges, writing the packet and stopping. The `ideate` skill also reads it when a
human chooses an idea.

Part of the ideation brief. See [`README.md`](./README.md).

---

All paths here are inside `round-<r>/`, the round whose verdict chose the
shortlist. The ledger is `round-<r>/shortlist/plan.md`.

## Deepen

Plan the `one-pagers` group from the IDs in `verdict.md`, then dispatch one
subagent per ID. Each writes `shortlist/ID.md`.

**What it gets:**

- its idea
- its red-team file
- the judges' questions and champion sentences about it
- `brief.md` and `FEEDBACK.md`

It gets no scores and no other shortlisted idea. It is warm to the critique on
purpose, because answering the critique is part of its job.

**Brief it on what the one-pager is for.** A human will use it to choose the
direction for the rest of the hackathon, and the build loop will turn it into
a spec. So it has to be concrete enough for both. Pass it the [hard
rules](RULES.md#hard-rules) in full. **It writes no code and no mockups, and it
publishes nothing.**

**The one-pager covers:**

- **Name and one-liner.**
- **The story.** The person, the moment, and why now.
- **The demo script.** Three minutes, beat by beat, with timestamps. Mark the
  wow beat. For every live dependency, give the fallback that keeps the demo
  alive.
- **What judges will ask**, and the answers.
- **The prize stack.** Each prize, its requirement quoted with a source, and how
  this idea meets it.
- **Architecture and suggested stack.** Name the hard part, and how the build
  loop will make it work.
- **Dependencies, spiked.**
  - Verify each dependency now, using web search and page fetches against docs,
    pricing pages and endpoints that need no key: it exists, it can be accessed,
    its cost and limits. Give URLs.
  - Anything needing a sign-up or a key becomes a **human action**, with a link.
  - Say which dependencies can be faked while a key is pending.
- **The build plan**, in the build loop's milestones
  ([the hackathon pack](../README.md#milestones)), with rough sizes, and
  **what to cut first**.
- **The UI concept.** The hero screen described concretely enough to sketch,
  the visual direction, and the products whose tone it borrows. Hold it to the
  `ui-craft` skill's standards, in words only.
- **Risks and mitigations.**
- **Why it wins, and why it could lose.** Honestly.

## Head judges

Once the one-pagers are complete, plan the `head-judges` group and dispatch
**three** head judges, cold. If the shortlist is empty, plan
`planned: head-judges none` instead. Each
reads `brief.md`, `FEEDBACK.md` and the one-pagers, but no scores, and writes
`shortlist/head-N.md`:

- the options ranked head to head, with reasons
- a recommendation
- the single question it would want the human to answer before committing

Three votes show whether the recommendation is clear or contested. A tired human
will otherwise take a single judge's pick as settled.

## The packet

1. **Write `round-<r>/PACKET.md`**, then copy it to
   `hackathon/ideation/PACKET.md`, which is the one the human opens. Lead with
   the choice:
   - **The recommendation.** The head judges' vote (3–0, 2–1, or split), and the
     reasons in two sentences. Where the vote and the board disagree, show both.
   - **A table**, one row per option: ID, name, one-liner, board score, picks,
     champion votes, red-team verdict, prizes stacked, human actions needed.
   - **For each option**, a five-line summary and a link to its one-pager.
   - **The trade-off that matters** between the top options.
   - **What the search covered.** Rounds run and their kinds, how many
     candidates were considered, what was cut and why (briefly), which budgets
     were hit, which files were abandoned, and which assumptions are still
     unverified.
   - **Feedback that is not yet reflected**: any entry that arrived after this
     round started.
   - **Lessons** from `LESSONS.md` that the human may want to apply to the
     brief.
   - **How to respond:**
     - `/harness-ideate decide ID [notes]` to choose
     - `/harness-ideate refine feedback` for another round
2. **Tell the human.** Send a push notification if this environment can, with
   the recommendation in one line. If this environment can publish a **private**
   page, you may also publish the packet there, never shared and never pinned,
   and include the link. Do not use a file-sharing connector. Otherwise the
   final message of the lap carries it.
3. **[Stop](RULES.md#stopping)**, and truncate the log. **The exception:** a
   `refine` entry arrived while the shortlist was being built, so no round has
   consumed it yet. Keep the loop running and tell the human. The next lap
   starts the refine round.

**An empty shortlist still gets a packet.** It says why nothing survived: dead,
vetoed or abandoned, with the reasons. It suggests the refine that would help
most.

## The handoff: `/harness-ideate decide`

The `ideate` skill runs this when the human chooses. It is not a loop lap.

The ID may come from any round's packet. If it does not come from the latest
one, confirm with the human first.

**Write `hackathon/DECISION.md`:**

- **At the top, the human's notes, verbatim.** They override the one-pager
  wherever the two conflict.
- **The chosen one-pager**, copied in full. The build loop runs where
  `hackathon/ideation/` does not exist.
- **The runner-up options**, by name and one-liner, as fallbacks.
- **Human actions still outstanding**, with links.

Then append a `decide` entry to `FEEDBACK.md`, stop any ideation job that is
still scheduled, and delete `LOCK`.

`DECISION.md` is gitignored. The build loop's spec PR adds it deliberately,
once hacking has started. Until then it never leaves this machine.
