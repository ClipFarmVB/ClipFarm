# Writing a design direction (`DESIGN.md`)

`DESIGN.md` is the design direction every implementer builds against and every
reviewer checks against. It has to be decisive. A direction that offers
options gets implemented five different ways by five different agents.

Derive it from the product's UI concept (in a hackathon, the chosen idea in `DECISION.md`) and the
primary path in the spec (in a hackathon, the demo script). It contains:

- **Tone and references.** One or two real products whose feel fits, and
  exactly what is borrowed from each (for example, "Linear's density and
  keyboard-first navigation; Vercel's black-and-white restraint").
- **The primary theme, and why.** Light projects better onto washed-out
  screens. Dark reads as premium on video. Choose for where the product will be
  seen. Ship the other theme only if it costs little.
- **Tokens**, written out as the CSS variables or Tailwind theme the scaffold
  will use:
  - **Colour:** a neutral scale, one accent (with hover and active shades),
    success, warning and danger, surface levels, and borders
  - **Type:** the families; a scale with size and line height per step
    (typically 12, 14, 16, 20, 24, 32, 48); the weights in use (two or three)
  - **Space:** a 4px base, and the steps in use
  - **Radius:** one or two values
  - **Elevation:** at most two shadows
  - **Motion:** durations (fast ~150ms, base ~200ms, slow ~300ms) and the easing
  - **Breakpoints**
- **Layout.** The app shell, the navigation pattern, content width, the grid,
  and how a screen's single primary action is placed.
- **Components.** The library, the customisations made to it, and the icon set.
- **The signature element.** One distinctive visual that makes the product
  recognisable in a gallery of fifty screenshots, such as a particular
  visualisation, a live waveform or an accent treatment. Tie it to the hero
  moment.
- **The hero screen, specified.** A layout sketch in words or ASCII, what is on
  screen at each step of the primary path, and what animates and why.
- **Data and copy voice.** What the fixtures look like, and how the product
  speaks.
- **Don'ts for this product**, beyond the generic list in the skill.

Keep it to what an implementer needs to make a consistent decision. Leave out
anything a capable engineer would do anyway.
