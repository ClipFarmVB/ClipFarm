# Judge: pool, score, attack, decide

Read on a lap that **pools, screens, judges, aggregates, red-teams, writes a
verdict, or closes out a round**.

Part of the ideation brief. See [`README.md`](./README.md).

---

## Pool

The orchestrator writes `round-<r>/pool.md` by merging the round's generator
files. A scratch script that concatenates them is fine, and better than
retyping, since retyping paraphrases.

- **Give every distinct idea an ID**, `R<r>-<nn>`, and keep its write-up
  verbatim.
- **Merge only true duplicates**: the same user and the same core mechanism.
  Keep the stronger write-up and note the other lens. Near neighbours stay
  separate, marked `near:`.
- **Remove ideas that break an eligibility rule or a `FEEDBACK.md` veto**, and
  list them at the bottom with the reason.
- **In rounds 2 and later, add two anchors.** These are candidates from the
  combined board that are already scored, one strong and one middling.
  - **Never pick one that has been shortlisted.** Feedback may have turned the
    judges against it, and the aggregate would read that as scale drift.
  - Give them fresh IDs in this round's sequence, so judges cannot tell them
    from new ideas.
  - **Record the mapping in `plan.md` when you write the pool**
    (`anchor: R2-09 = R1-04`). Judges never see `plan.md`.

  Anchors let the aggregate detect a panel that is scoring on a different scale
  this round.
- **Write `pool.md` complete, then plan the next group in the same lap.** That
  is either `screen` or `judges`.

**Screen pools larger than about 30.**
- Plan and dispatch two cold screeners (`screen-a.md`, `screen-b.md`).
- Each marks every idea `keep` or `cut`, with one line of reasoning. It judges
  only the demo moment, fit, and resemblance to the saturation list.
- An idea is cut only if both screeners cut it. Anchors are never cut.
- Judges then receive the surviving IDs.

## The rubric

Each criterion is scored from 1 to 5.

| key | criterion | 1 | 3 | 5 |
|---|---|---|---|---|
| **W** | Demo moment: will a judge remember it tonight? | a form and a table | nice, if you're paying attention | people lean forward |
| **F** | Track and prize fit | the sponsor tech is decoration | fits one track well | the tech is essential and prizes stack naturally |
| **N** | Novelty, against `brief.md`'s saturation list, including what assistants suggest | on the list | a known shape with a real twist | nothing on the list resembles it |
| **P** | Problem and story | "everyone who…" | a real user, a vague pain | a specific person and moment, told in one breath |
| **D** | Visible technical depth | CRUD plus an API call | one hard part, visible to a judge | clearly hard, and clearly working |
| **X** | Executability (5 = low risk) | needs access that cannot be had in time | real but fakeable dependencies | everything verified available |
| **U** | UI leverage | little on screen to judge | a standard app surface | a surface where a polished UI will show and matter |

**Default weights:** W 3, F 2, N 2, P 1.5, D 1.5, X 1, U 1.

`brief.md` re-maps these weights onto the event's published criteria. Use the
brief's weights, then apply any preference in `FEEDBACK.md` on top.

X weighs little on purpose. The build loop fakes every live dependency, and the
red team owns feasibility.

## The panel

**Each persona scores W, plus the criteria it owns.** Every judge scores the
demo moment, because every real judge reacts to it. Beyond that, a persona
scores only what it is qualified to judge. This keeps four same-model judges
from blurring into one, while still giving every criterion two independent
scores.

| persona | who it is | owns |
|---|---|---|
| sponsor engineer | knows the technology deeply; rewards essential, non-obvious use; knows its limits | F, D, X |
| staff engineer | skeptical about what breaks, on stage and in the build | F, D, X |
| veteran judge | has seen a thousand projects; knows what has already shipped | N, P, U |
| product and design lead | asks whether someone would use it, and whether the story and the screen land | N, P, U |

When `brief.md` lists the event's real judges, map them onto at most five
personas. Each persona owns the criteria that match its background, and **every
criterion must have at least two owners.**

**If every judge that owns a criterion is abandoned, the round cannot be
scored. Stop and notify the human.** Silently dropping a criterion would change
every score in the round.

Each judge writes `round-<r>/judge-PERSONA.md`. **Give each judge the pool in a
different order**: rotate the list so each judge starts at a different point.
That way the ideas a judge scores carelessly at the end are different ideas for
each judge.

For each candidate, the judge writes one line the aggregate can parse, followed
by its reasons:

```
R2-07 | W 4 | F 5 | D 3 | champion: yes | q: How does it work offline?
- W: …  - F: …  - D: …
- champion: "This is the one where the sponsor's API is the product, not a feature."
```

- **champion.** At most **two** per judge. Each champion names the sentence the
  judge would say in deliberation.
- **q.** The first question the judge would ask the team.
- **Top five.** At the end of the file, each judge writes a line
  `top5: ID, ID, ID, ID, ID`, ranked. If the pool has fewer than ten ideas,
  it picks its top three instead.

Judges are cold, per [independence](RULES.md#independence-is-what-the-panel-is-for).
They do not search the web, because prior art is the red team's job. N is
scored against the saturation list **and** against the products and projects
the judge already knows.

## Aggregate

The orchestrator writes `round-<r>/board.md` using a scratch script, not by
hand. For each candidate:

- **The criterion score** is the mean of the judges who scored it.
- **The overall score** is `100 × Σ w·(c − 1)/4 ÷ Σ w`, summed over the
  criteria that were scored.
- **Picks** is the number of judges whose top five include it.
- **Champions** is the number of champion votes, with the sentences quoted.
- **The judges' questions**, listed.

**Anchors.** Read the anchor map from `plan.md`, and compare each anchor's new
score with its canonical score on the combined board.
- **If both moved the same way by more than 10 points**, subtract their mean
  shift from every other score this round. Record the shift in `board.md`,
  since every later recompute of this round's scores applies it again.
- Anchors keep their canonical scores, and are left out of the cut and the
  convergence test.

**The red-team cut:**

- the top K+2 of this round's candidates by score, where K is `shortlist size`
  (4 by default)
- plus up to two more, taking those with the most champion votes plus picks
  first, among candidates with at least one champion vote or at least two
  picks

A mean punishes an idea that one judge loves and another dismisses, so a
champion or a pick is a signal worth testing.

**After writing `board.md`**, plan the `red-team` group from the cut, and
dispatch it in the same lap. An empty cut is planned as
`planned: red-team none`.

## Red team

Dispatch one adversary per candidate in the cut, each writing
`round-<r>/red-ID.md`. It gets the candidate, `brief.md` and `FEEDBACK.md`,
and no scores. It is the loop's owner of feasibility and prior art. It checks:

- **Prior art.** Search the Devpost galleries, GitHub, Product Hunt and Hacker
  News. For each close match: the link, how close it is, and whether it won.
- **Feasibility.** Access, pricing, rate limits, terms, data availability,
  latency and hardware, each against a source. Ask whether a realistic fake
  could stand in during development and on stage.
- **Demo fragility.** What could fail live, and what the fallback is.
- **Eligibility.** Every hard requirement of the tracks the idea claims.
- **The first objection** a judge would raise, and whether there is an answer.
- **The rescue.** The smallest change that fixes the worst problem.

It ends with its adjusted **N** and **X**, each on the 1–5 scale with a reason,
and one verdict:

- **dead**, only for a **sourced hard blocker**: the idea is ineligible for
  every track it targets, access cannot be obtained before the deadline, or an
  essentially identical project already won at this event or for this prize.
- **wounded**, for a real but fixable problem, with the rescue. A merely
  similar project makes an idea wounded, with "twist required".
- **survives**, otherwise.

Every claim follows the [evidence rule](RULES.md#evidence).

## Verdict

The orchestrator writes `round-<r>/verdict.md` by applying these rules in
order.

1. **Adjust.**
   - Replace each attacked candidate's N and X with the red team's.
   - Recompute its score, then apply the round's anchor shift again.
   - Dead candidates are out.
   - Re-apply every `FEEDBACK.md` veto.
2. **Update the combined board**, and write it in full into `verdict.md`. It
   lists every live candidate across all rounds, with its adjusted score,
   picks, champions, red-team status and lineage.
   - **Retire a parent** once an evolved child that has been red-teamed and
     is not dead scores above it.
   - A child that was never attacked retires nothing.
3. **Choose the next step:**
   - an **unconsumed `refine` entry** exists → `refine`
   - this is a refine round, or the round budget or time budget is spent
     ([budget](RULES.md#budget)) → `shortlist`
   - this is round 1 → `evolve`. Its wounds are what round 2 exists to repair.
   - no candidate from this round, anchors excluded, entered the combined top
     K → `shortlist`. The search has converged.
   - otherwise → `evolve`
4. **For `evolve`, name the seeds**: the live, red-teamed candidates in the
   combined top K+2. For each, carry its status, its critique (the red-team
   notes and the judges' questions) and its champion sentences into
   [`GENERATE.md`](GENERATE.md#evolve-rounds).
5. **For `shortlist`, choose it:**
   1. **Start from eligible candidates**: live, red-teamed, and not retired.
      **In a refine round**, a candidate that appeared in an earlier packet is
      eligible only if the feedback names it favourably. The human has already
      seen and passed over the rest.
   2. **Take the top K by score.**
   3. **Keep them distinct.** At least `min(3, K)` of them must differ in both
      the "Who" and the "How it works" fields of the schema. If two share a
      direction, keep the higher one and take the next one down. When unsure,
      treat the pair as distinct.
   4. **Add a wildcard** if one exists: the eligible candidate outside the top
      K with the most champion votes plus picks, provided it has at least one
      champion vote.
   5. **Write the chosen IDs into `verdict.md`**, then plan the `one-pagers`
      group ([`SHORTLIST.md`](SHORTLIST.md)). An empty shortlist is planned as
      `planned: one-pagers none`, and the packet says why nothing survived.

**Closing out a round** (row 4 of [the phase
machine](RULES.md#the-phase-machine)):

1. Append `abandoned: round — budget spent` to `plan.md`.
2. Write `verdict.md` with the next step `shortlist`. Copy the combined board
   forward from the last complete round's verdict, and choose the shortlist
   from it.
