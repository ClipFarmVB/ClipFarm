# UI review rubric

Used by review rounds on PRs that change anything visible, and by rehearsals.
Review from **screenshots you captured in this session** at 1440×900, 1280×720
and 390×844, and from using the running app. Never review from the code
alone.

## Tiering

The tiers decide what blocks a merge, so they are held to defects any reviewer
would agree on. That keeps successive fresh reviewers from each finding a new
matter of taste and blocking the PR over it.

- **Critical.** A primary-path step that breaks or cannot be read: a crash, a blank
  screen, unreadable text, or the key content off-screen at 1440×900.
- **Medium.** An **objective** defect on a **primary-path** screen at 1440×900.
  The primary path is defined in the `ui-craft` skill (in a hackathon, every screen the demo script shows).
  - an unhandled state
  - placeholder or test data
  - overlap, clipping or truncation
  - contrast below AA
  - a raw value outside the tokens
  - broken alignment
  - no feedback on an action
  - an entry from the generic-tells list in the skill
- **Nit.** Everything else, which is still worth listing: taste, the 1280×720
  and 390×844 viewports, and screens off the primary path. A nit worth doing
  becomes a suggested polish card.

## What to check

- **Hierarchy.** Within two seconds, can you tell what the screen is for and
  what to do next? Is the primary action the most prominent thing, and the only
  prominent one?
- **Alignment and spacing.** Shared edges line up. Padding is consistent
  within each component type. There are no cramped or stranded elements.
  Spacing is on the 4px grid.
- **Typography.** Only scale steps and weights from the design direction are used.
  Nothing is truncated unintentionally. Line lengths stay readable. Data uses
  tabular numbers.
- **Colour and contrast.** The accent is used only for its purpose. Text passes
  AA. There are no raw values outside the tokens. Both themes hold up, if both
  ship.
- **States.**
  - Empty, loading, error and success are present and designed.
  - A skeleton matches the final layout.
  - AI or long-running output streams.
  - A spinner alone never shows for more than about a second.
- **Data realism.** No placeholder or test data, no zeros standing in for
  content, and dates, numbers and names that look plausible.
- **Feedback and motion.** Every action responds immediately. Motion explains
  change, at a restrained duration, with no layout shift or jank.
- **Responsiveness.** The layout holds at all three sizes, with no horizontal
  scroll and no overlap.
- **Consistency.** Components, radii, icons and copy voice match the design direction
  and the rest of the app.
- **Generic tells.** Check against the list in the skill.
- **Copy.** Specific, sentence case, no filler, and no exclamation marks.
- **Accessibility basics.** Focus is visible, controls are labelled, and hit
  targets are at least 40px.

## Output

For each screen reviewed, give:

- the screen name
- the viewport
- the path of the screenshot
- the findings, each with its tier, what is wrong, where it is (the component,
  and the `file:line` that renders it), and what good looks like

End with the single change that would most improve how the primary path looks.
