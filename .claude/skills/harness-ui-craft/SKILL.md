---
name: harness-ui-craft
description: (Repo-pinned copy of the harness plugin's ui-craft skill, for sessions without the plugin; prefer harness:ui-craft where the plugin is installed.) Standards and process for building advanced, mature product UI, covering the design direction, building screens, the visual review rubric, and screenshot-based verification. Use whenever writing or changing anything a user sees, writing a design direction, reviewing a PR that touches UI, rehearsing a demo, or when the user asks to make the UI better, more polished, or less generic.
---

# UI craft

People form an opinion of a product in its first ten seconds on screen, before
they understand what it does. A mature UI signals a finished product and makes
the core flow easy to follow, since the eye lands where the story is. Two
working products are usually separated by exactly this.

**"Mature" means restraint and completeness, not decoration.** The bar is a
funded product (Linear, Vercel, Stripe, Raycast), not a template. **"Advanced"
means the interface makes the product's core mechanic tangible**: output that
streams in with structure, a change you can watch happen, direct manipulation.
It does not mean effects added on top.

## The design direction comes first

Every screen is built and reviewed against one written design direction.
**If the repo already has a design system** (tokens, a component library, a
brand guide), that is the direction: follow it, and never introduce a second
one alongside it. Only a repo with none gets a new `DESIGN.md`, written per
[`references/design-direction.md`](references/design-direction.md).

**The primary path** is the set of screens that matter most: in a hackathon,
the screens the demo script shows; in a product, its core flows. The rubric
holds those screens to a higher bar.

## Who reads this for what

| you are | read |
|---|---|
| writing a design direction for a repo that has none | [`references/design-direction.md`](references/design-direction.md), then the standards below |
| an implementer building a screen | the standards below, and the repo's design direction |
| a reviewer or rehearser | [`references/rubric.md`](references/rubric.md) |
| a human asked for polish directly | [working standalone](#working-standalone) |

## Standards

- **One direction, written down, expressed as tokens.** Components use the
  direction's tokens, never raw hex values or one-off pixel sizes. Most
  inconsistency starts as a single hard-coded value.
- **Each screen has one job and one primary action.** Decide what the user
  should look at on that screen, and make it the heaviest thing on it.
  Everything else steps back.
- **Colour carries meaning.** Use neutral surfaces and a single accent,
  reserved for the primary action and the key state. Semantic colours
  (success, warning, danger) are used only for their meaning.
- **Typography does most of the hierarchy.** One family, plus a monospace for
  data or code. A small scale. Weight and colour do more of the work than
  size. Numbers in data use tabular figures.
- **Spacing is systematic.** Use a 4px base and consistent padding inside each
  component type. Align everything to shared edges. Make density suit the
  task: data tools can be dense, while a hero moment gets room.
- **Every state is designed.**
  - Empty states explain and offer the next action.
  - Loading uses a skeleton that matches the final layout, and AI output
    streams in progressively.
  - Errors say what happened and how to recover.
  - Success is confirmed.
  - Long text, and zero, one or many items, all still look right.
  - A spinner alone for more than a second reads as a hang.
- **The data looks real.** Fixtures tell a plausible story, with realistic
  names, numbers, dates and content. Never "Test User", lorem ipsum, or a
  dashboard full of zeros.
- **Every action gets feedback within 100ms.** Prefer optimistic updates. Use
  motion to explain change (enter, exit, reorder, expand), at 150–250ms with
  ease-out and without bounce, and respect `prefers-reduced-motion`.
- **The hero moment is designed like a keynote slide.** It is big, focused,
  readable from across a room, and it animates deliberately.
- **Add advanced interactions only where they serve the core mechanic.**
  Options include a command palette, keyboard shortcuts, streamed structured
  output, inline editing, undo toasts, drag and drop, and live presence. Pick
  the few that make this product's idea felt.
- **Copy is part of the design.** Use specific verbs, sentence case and short
  labels. No exclamation marks, and no "Welcome to X!" on an empty dashboard.
- **Basic accessibility signals maturity.**
  - text contrast of at least AA
  - visible focus
  - semantic elements
  - labelled controls
  - hit targets of at least 40px
- **The primary path has to feel fast.** No layout shift, preloaded fonts,
  sized images, and no blocking work on first paint.

**Default stack, for a new React project with no design system:** Tailwind
with CSS-variable tokens, shadcn/ui (Radix) components, `lucide-react` icons,
and `motion` for transitions. If a data-visualisation skill is available, use
it for charts. The standards hold on any stack.

## What makes a UI look generic

People have seen these hundreds of times, so each one marks a product as a
template:

- purple-to-blue gradients on everything
- glassmorphism
- a glow behind every card
- emoji standing in for icons
- a centred hero above three feature cards
- default-blue buttons
- every card given a border, a shadow *and* a gradient
- mixed corner radii
- grey text on a grey background
- five or more font weights
- a spinner as the only loading state
- a modal for every interaction
- an enormous empty page around tiny content

## Look at it

**Never call a UI finished without looking at a screenshot taken in this
session.** Code that reads correctly still renders wrong.

- Use the repository's screenshot script if it has one. Otherwise write a
  throwaway Playwright script in a scratch directory.
- Capture every screen you changed at **1440×900** (desktop), **1280×720**
  (video / small laptop) and **390×844** (mobile), in each theme the product
  ships.
- Look at each screenshot against the design direction and the rubric. Fix
  what you find, then capture again. Two passes are normal. Stopping after the
  first capture is how obvious defects get shipped.
- Exercise the states as well as the happy path. Seed an empty account, force
  an error, and slow the network.

## Working standalone

When a human asks directly for polish:

1. Run the app, and capture the primary-path screens.
2. Audit them against [`references/rubric.md`](references/rubric.md).
3. Present the findings, most visible first.

**If a harness loop is running**, file the findings as `ui` tickets through
the loop's tracker, rather than editing files the loop's agents may be working
in. Otherwise, fix the top findings, capture again, and show the before and
after.
