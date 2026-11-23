# Generate: diverging, then evolving

Read on a lap that **starts or continues a round's generation**: the first
round, an evolve round, or a refine round.

Part of the ideation brief. See [`README.md`](./README.md).

---

## What a strong idea looks like

Every generator is briefed with this standard, in full.

- **A specific person with a specific bad moment** beats a platform for
  everyone. Judges remember a story, not a market.
- **The sponsor's technology carries weight.** Remove it and the product
  breaks. A bolted-on integration earns nothing from the sponsor's judge.
- **The demo moment is something happening on screen, live**, and a judge sees
  it within the first thirty seconds. A slide is not a demo moment.
- **Scale ambition to the build loop.** Implementation is cheap now, and
  breadth, polish and a mature UI fit inside the timebox. Do not shrink an
  idea to fit a weekend of hand-coding.
- **Live dependencies are fine when they can be faked.** The build loop puts
  every external service behind a realistic fake, and that fake doubles as the
  demo's fallback. What really threatens an idea is access that cannot be had
  before the deadline: approvals, hardware that has to ship, data that does not
  exist.
- **Be unlike what every other team will build.** `brief.md`'s saturation list
  includes what a frontier model suggests for these tracks, which is what most
  teams will build. An idea that resembles an entry there needs a twist that is
  visible in the first ten seconds.
- **Stack prizes only where they fit.** One product built around one sponsor,
  which also happens to meet another track's requirements, beats a product that
  bolts three sponsors on.

## The idea schema

```
### working title
- One-liner:
- Tracks and prizes it is eligible for:
- Who, and their bad moment:
- The demo moment (what a judge sees in the first 30 seconds):
- How it works (name the technically hard part):
- Why now, and why it does not exist yet:
- External dependencies (APIs, data, hardware, access), with sources:
- Assumptions not yet verified:
- Biggest risk:
- Lineage: (evolve and refine rounds: parent IDs, and what changed)
```

## Lenses

A lens is a starting place, and several lenses per round give the round its
diversity. Pick the ones this event rewards, and invent new ones when the event
calls for it. A first round typically uses five to seven.

- **sponsor-native.** What would this sponsor feature on its own homepage? Use
  its recent launches, in a way its docs do not already demo.
- **demo-first.** Start from a ten-second moment that makes a room react, and
  work backwards to a product.
- **person-and-pain.** Start from real people and a concrete, observable bad
  moment. Search the forums where they complain.
- **capability-frontier.** What became possible in the last year that almost
  nobody has made into a product? Agents acting in the world, real-time voice
  or vision, on-device models, new APIs.
- **contrarian.** The idea judges will not see twice. Playful or surprising,
  and still useful to someone.
- **prize-stacker.** One product that qualifies for the main track and several
  sponsor prizes without feeling assembled.
- **underused-data.** A public dataset or API nobody has used well.
- **unfair-advantage.** Start from what the builder knows or cares about, per
  the brief, that other teams will not.

## The generator's brief

Tell it what the ideas are for: a solo builder with an autonomous build loop,
trying to win this event. Point it at `brief.md` and `FEEDBACK.md`, saying what
each contains. Then give it:

- the standard above
- its lens
- the schema
- its output path
- the [hard rules](RULES.md#hard-rules)
- the completion marker

It works in two passes:

1. **Brainstorm widely**, around twenty rough ideas, without self-censoring.
2. **Keep only the best three to five.** Discard anything on the saturation
   list, anything that breaks a `FEEDBACK.md` veto, and anything dull. Write up
   only the survivors.

The wide first pass is what gets it past the model's first, most common
thoughts. It may search the web to ground an idea in real pain or to check its
novelty, and it cites what it used.

## Starting a round

Write `round-<r>/plan.md` before dispatching anything. It records:

- `kind: first`, `evolve` or `refine`
- for `refine`, `consumes:` followed by the feedback entry's heading
- one line on why this set of generators suits the event or the feedback
- the `generators` group as `planned:` lines, one per generator, each with its
  lens or seed ([ledger](RULES.md#files-ledgers-and-completeness))
- for a refine round that needs research, the `refine-research` group as
  `planned:` lines too

Then dispatch. The generators go out in this lap, unless research comes first
(see below).

Output paths are `gen-LENS.md` for lens generators and `gen-KIND-SEEDID.md` for
seeded ones, so two generators never share a file.

### Round 1

`kind: first`. The lenses, chosen for the event.

### Evolve rounds

`round-(r-1)/verdict.md` names the seeds. Each seed comes with:

- its critique: the red-team notes and the judges' questions
- **what its champions said they would argue for**

Assign generators mechanically, not by taste:

- **Every seed marked `survives` gets an `amplify` generator.** It makes the
  demo moment bigger, sharpens the story, or adds a prize where one fits
  naturally. It keeps what the champions liked.
- **Every seed marked `wounded` gets a `repair` generator.** It removes the
  wound, following the red team's rescue where one was given. **It keeps what
  the champions liked.** A repair that sands off the edge that won the vote has
  failed.
- **The top survivor also gets a `transplant` generator.** It takes one
  mechanism from another survivor and puts it into this idea's story, keeping
  one person and one moment. Merging two whole products produces feature soup.
- **One `gap` generator** is given only `brief.md`, `FEEDBACK.md` and the
  titles in the combined board, with no scores. It aims at what the pool is
  missing.

Seeded generators return **one or two** ideas per seed, not five. More would
fill the next pool with near-copies of the same few seeds. Each idea records its
lineage.

### Refine rounds

A refine round starts from a `refine` entry in `FEEDBACK.md` that no round has
consumed. Write `kind: refine` and `consumes:` into `plan.md` first. Then:

- **Seed from the feedback.** Ideas the human referenced get `repair` or
  `amplify` generators pointed at what they said.
- **Add lenses that answer the request**, plus at least one fresh generator
  that knows only the brief and the feedback. The human may be reacting
  against the whole pool.
- **Research first if the feedback names something the research does not
  cover**, such as a sponsor or technology never looked at.
  - The lap that starts the round dispatches only the `refine-research` group.
  - When that group is complete, append what it found to `brief.md` under a
    *Refine addendum* heading, using the `.tmp`-and-rename write so the file
    stays complete.
  - The generators go out on the next lap (row 6 of [the phase
    machine](RULES.md#the-phase-machine)).
- **Quote the feedback in full** in `plan.md` and in every brief of the round.

A refine round's verdict goes straight to the shortlist, unless another
`refine` has arrived since. The human is waiting for an answer.
