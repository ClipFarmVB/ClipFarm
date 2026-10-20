# Intake: from track descriptions to a brief

Read on an **intake lap**: any lap where `hackathon/ideation/brief.md` is not
complete yet.

Part of the ideation brief. See [`README.md`](./README.md).

---

## What intake produces

`hackathon/ideation/brief.md` is the one document every generator and judge
reads. It should let someone who has never seen the event reason like a
competitor who has studied it: what is being asked, what earns a prize, what
the sponsors want to see, what judges have seen too often, and what constrains
the idea. Its research lives in `research/`, one file per topic, each with its
sources, tracked in the `research/plan.md` [ledger](RULES.md#files-ledgers-and-completeness).

## Can this run start?

Read `hackathon/EVENT.md` and everything in `hackathon/tracks/`, fetching every
URL in `links.md`. **Stop and ask the human** (log it, notify them, then stop
the loop) if any of these is true:

- **No track text could actually be read.** Pages that render with JavaScript
  often come back as empty shells. A `links.md` whose pages yielded nothing
  counts as no tracks, and the human should paste the text instead.
- **`EVENT.md` still says `TODO`** where the submission deadline or the judging
  criteria belong.

Those are the inputs nothing downstream can guess. Any other gap gets a default
and an open question in the brief. A field still at `TODO` is unknown, not a
constraint.

## Research

**Dispatch `event` and `saturation` first, in a group of their own**, before
any sponsor researcher. A web-search budget is usually **session-wide, not per
subagent**, so within one group the dispatch order is a priority order: a run
that sent eighteen researchers at once spent the whole budget on sponsors and
left these two with nothing. They are the two files every later round depends
on most, since they decide novelty and judging fit.

**When a page cannot be fetched, try the vendor's documentation repository on
GitHub.** Most vendor docs sites are built from an open-source repo
(`cloudflare/cloudflare-docs`, Elastic's `docs-content`), which serves the same
primary content through a different host. That is a second route to the source,
not a way around a policy denial: **a refusal is reported, never worked
around**.

Plan the `research` group in `research/plan.md`, then dispatch it
([dispatching](RULES.md#dispatching)). Scale the split to the event. A typical
split:

- **One subagent per sponsor**, or per group of small sponsors, writing
  `research/sponsor-NAME.md`. It covers:
  - what the product does
  - what the sponsor launched recently, which is usually what its developer
    relations team wants used
  - how a developer gets access (free tier, keys, waitlists, rate limits,
    cost)
  - how good the quickstart is
  - what past winners of this sponsor's prizes built
- **The event** → `research/event.md`:
  - past editions' winners and what they had in common, described as shapes
    judges rewarded
  - the judges' backgrounds, if listed
  - every rule that constrains ideas
- **Saturation** → `research/saturation.md`: the project shapes judges in this
  domain see over and over, found by searching past galleries for the tracks'
  themes, with links.
- **What every team's assistant suggests** → `research/assistant-defaults.md`.
  Most teams paste the track text into a frontier model and build its first
  suggestions. Your generators and judges are that kind of model too, so these
  ideas both appear everywhere and score well with same-model judges.
  - Dispatch two or three subagents.
  - Give each the hard rules, its output path (`research/naive-N.md`), the
    completion marker, the track text, and the instruction "list ten project
    ideas for this track", and nothing else. No event brief, no standard, no
    lens: their naivety is the point.
  - When they are done, the orchestrator collects their lists, unedited, into
    `research/assistant-defaults.md`.

Tell each researcher what the brief is for: a builder planning a weekend. A
pricing page matters only as far as it tells that builder what they can use.

## Writing the brief

The orchestrator writes `brief.md` from the research and the event inputs.
Synthesis is not idea generation. **The brief proposes no project ideas.** The
past winners and the assistant defaults appear only as things to avoid or to
beat, never as templates.

It covers:

- **The event at a glance.** The timebox, a solo team, the submission
  requirements, and the eligibility rules that constrain ideas.
- **How judging works.** The published criteria and weights, mapped onto the
  internal rubric in [`JUDGE.md`](JUDGE.md#the-rubric), with the resulting
  weights written out.
- **Each track.** The ask, quoted briefly. The prize. The hard requirements.
  What the sponsor most likely wants to see, labelled as inference.
- **The prize-stacking map.** Which prizes one project can hold together,
  according to the rules, with the source.
- **The sponsor-technology cheat sheet.** Capability, access, cost, limits and
  URL for each technology.
- **What has won before**, as the shapes judges rewarded.
- **The saturation list.** The researched archetypes, plus a section headed
  *What every team's assistant suggests* holding the assistant defaults.
  Generators discard ideas on this list, and judges score novelty against it.
- **The builder.** Skills, interests, hard constraints, and the accounts and
  keys already held (names only), taken from `EVENT.md`. Add every
  `FEEDBACK.md` entry, quoted.
- **Open questions** for the human. None of them blocks the run.
- **Inputs.** Each file read from `hackathon/tracks/`, plus `EVENT.md`, with its
  modification time in UTC ISO form. The skill uses this list to notice when the
  inputs have changed since intake.

Write it to `brief.md.tmp`, end it with `<!-- complete -->`, then rename it.
