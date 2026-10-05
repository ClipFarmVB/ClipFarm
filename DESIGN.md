# ClipFarm design direction

The one design direction for the web app (`web/`). Every UI change is built
against it and every UI review checks against it (CF-576). Where it and the
code disagree, the code is out of date: change the code, or change this file
in the same PR on purpose, never silently.

It is decisive on purpose. Each decision has one answer, so five people (or five
agents) building five screens end up with one product.

---

## Tone and references

**Courtside broadcast.** The feel of a good sports broadcast graphic: calm
chrome, real footage, and numbers that matter set large and confident. The
product's job is to turn a full game into the plays worth watching, so the
footage and the ranking carry the screen and everything else steps back.

| Reference | What we take, exactly |
|---|---|
| **Veo** | A warm off-white canvas with one loud accent, and highlight markers on a game timeline. |
| **Hudl / Balltime** | The AI's work drawn *on* the footage and the timeline rather than described in copy; a clip list you can filter by facet chips. |
| **OpusClip** | One score per clip that the list sorts by, by default. |
| **Linear** | Inter configured rather than defaulted, dim navigation, few type sizes, and grouping by spacing instead of boxes. |

We do **not** take Hudl's Barlow or Pixellot's all-caps Oswald (both belong to
someone else), or Trace's orange CTA (Trace is already teal on cream; our
difference is type and the rally strip, not colour).

## Theme

**Light (cream) is the primary theme and the default** for every new visitor.
It is the brand (a teal line-art mark on cream), it is what the logo was drawn
on, and a permanent dark UI is one of the most-cited signs of a generated
product. Dark stays as a user choice: the tokens already carry it.

**Video never sits on cream.** Players, thumbnails and any frame of footage sit
on the media surface (`--cf-media`, near-black, the same in both themes), so
footage always reads as footage.

---

## Tokens

All values live in `web/src/app/globals.css`. Components use tokens only: no
raw hex, no Tailwind palette colours (`red-400`, `emerald-500`, `zinc-*`), no
arbitrary pixel sizes (`text-[11px]`, `h-[26px]`) outside the token
definitions themselves.

### Colour

The neutrals, brand and on-brand values are the ones CF-573 set; they stay.
This adds the roles that were missing, so nothing reaches for a palette class.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--cf-bg` | `#faf7f1` | `#0b0f0f` | Page |
| `--cf-surface` | `#f3eee4` | `#101616` | Raised areas, inputs |
| `--cf-surface-high` | `#ebe4d7` | `#172020` | Selected, pressed |
| `--cf-surface-hover` | `#e3dbcc` | `#1d2828` | Hover |
| `--cf-border` | `#e5ded1` | `#1f2a2a` | Dividers in dense lists only |
| `--cf-border-strong` | `#d2c9b8` | `#2f3d3c` | Input outlines, focus-adjacent |
| `--cf-fg` | `#1c1b17` | `#eef3f2` | Primary text |
| `--cf-muted` | `#65604f` | `#7f8c8a` | Secondary text: **the lowest colour any information may use** |
| `--cf-subtle` | `#948c7b` | `#56625f` | Disabled and decorative only: **never information** (≈3:1) |
| `--cf-brand` | `#037170` | `#3cc2b3` | The one accent: primary action, selection, success |
| `--cf-brand-hover` | `#025f5e` | `#5fd3c5` | Hover/active of a brand fill (renames `--cf-brand-light`) |
| `--cf-brand-strong` | `#025f5e` | `#3cc2b3` | Brand-coloured text on a brand tint |
| `--cf-brand-dim` | brand at 9% | brand at 12% | Brand tint behind brand text |
| `--cf-on-brand` | `#ffffff` | `#0b0f0f` | Text/icons on a brand fill |
| `--cf-rank` | `#b4470f` | `#f0904f` | **Play score and Top plays only** (the warm accent) |
| `--cf-rank-strong` | `#943a0c` | `#f0904f` | Rank text on a rank tint |
| `--cf-rank-dim` | rank at 10% | rank at 12% | Rank tint |
| `--cf-on-rank` | `#ffffff` | `#0b0f0f` | Text on a rank fill (score pill) |
| `--cf-warning` | `#8a5300` | `#e3a53b` | Warnings (quota, expiring source) |
| `--cf-warning-strong` | `#7a4900` | `#e3a53b` | Warning text on its tint |
| `--cf-danger` | `#b42318` | `#f2786a` | Errors and destructive actions |
| `--cf-danger-strong` | `#9c1e14` | `#f2786a` | Danger text on its tint |
| `--cf-media` | `#0b0b0b` | `#0b0b0b` | Behind all video and thumbnails |

**Success is the brand colour plus a check icon.** There is no separate green:
a second green beside the teal reads as the same colour (emerald-400 against the
dark brand is 1.14:1) and splits one meaning into two.

**Action types (spike, serve, dig, set, block) have no colour.** They are text,
in a neutral chip. Per-action colours collided with every semantic colour (red
meant spike, error and delete at once) and are what made the clip footer read
as decoration.

**Tints are fixed.** The only translucent brand/rank/warning/danger fills are
the `-dim` tokens. No `bg-brand/8`, `/10`, `/20`, `/25`, `/30`.

Contrast, WCAG relative luminance (measured when set):

| Pair | Light | Dark |
|---|---|---|
| brand text on bg / surface | 5.46 / 5.04 | 8.77 / 8.32 |
| brand-strong on brand-dim | 5.67 | 7.09 |
| on-brand on brand / brand-hover | 5.83 / 7.49 | 8.77 / 10.66 |
| rank text on bg / surface | 5.10 / 4.72 | 8.08 / 7.67 |
| rank-strong on rank-dim | 5.52 | 6.63 |
| on-rank on rank | 5.46 | 8.08 |
| warning text on bg / surface | 5.92 / 5.47 | 8.91 / 8.46 |
| warning-strong on its tint | 5.72 | 7.22 |
| danger text on bg / surface | 6.15 / 5.69 | 7.04 / 6.68 |
| danger-strong on its tint | 5.96 | 5.85 |
| muted on bg / surface | 5.88 / 5.44 | 5.53 / 5.24 |
| white / brand / rank on media | 19.68 / — / — | — / 8.96 / 8.25 |

### Type

Two families, each with one job.

- **Inter** for the interface, configured rather than defaulted: the optical
  size axis on (`opsz` 14–32, so large sizes get the Display cut),
  `font-feature-settings: "cv11", "ss03"`, and **tabular figures (`tnum`) on
  every time, count and score**.
- **Archivo**, condensed (`font-stretch: 75%`, weight 700 for display, 600 for
  stats), for two things only: **the landing page's display headlines** and
  **numbers that matter** (play scores, the game's clip count and length on its
  page). That condensed, broadcast-scoreboard number is the typographic voice.

Both load through `next/font/google` (preloaded, no layout shift), replacing the
`<link>` to Google Fonts in `layout.tsx`.

The scale is a set of named roles. A size that is not a role does not exist.

| Role | Family | Size / line height | Weight | Use |
|---|---|---|---|---|
| `display` | Archivo condensed | 56 / 56 | 700 | Landing hero headline only |
| `display-sm` | Archivo condensed | 32 / 36 | 700 | Landing section headlines only |
| `title` | Inter (Display cut) | 24 / 32 | 600 | One per page: the page title |
| `heading` | Inter | 16 / 24 | 600 | Section headings inside a page, card titles |
| `body` | Inter | 14 / 22 | 400 | Default text |
| `small` | Inter | 13 / 20 | 400 | Secondary text, help text |
| `caption` | Inter | 12 / 16 | 500 | Metadata, timestamps, chip labels: **the minimum size** |
| `stat` | Archivo condensed | 20 / 24 | 600 | Play score on a tile, numbers in headers |
| `stat-lg` | Archivo condensed | 40 / 40 | 600 | The game page's headline numbers |

Weights in use: 400, 500, 600 (and Archivo 700 for display). Nothing below 12px.
**No uppercase with wide tracking anywhere**: section labels are `heading` or
`small` in sentence case, column headers are `caption` in sentence case.

In Tailwind v4 these are `--text-<role>` and `--text-<role>--line-height` in
`@theme`, used as `text-body`, `text-caption` and so on.

### Space

4px base. Steps in use: **4, 8, 12, 16, 24, 32, 48, 64**. No 2/6/10/14px
half-steps (about a quarter of today's spacing utilities are), except a 2px gap
inside a chip or between stacked metadata lines.

Group with space, not boxes: related items sit 8–12px apart, groups 24–32px
apart, page sections 48–64px apart.

### Radius

Two values: **6px** (`--radius-control`: buttons, inputs, chips, menus) and
**10px** (`--radius-surface`: surfaces, media tiles, dialogs, the player). Full
round only for avatars and the score pill.

### Elevation

Two shadows: `--shadow-raised` (a media tile on hover, a dragged item) and
`--shadow-overlay` (dialogs, popovers, menus, toasts). Nothing else casts a
shadow.

### Borders

A border is for separating rows in a dense list and for outlining inputs. **A
surface is a fill (`surface`) or nothing; it is not also a border.** Never a
card inside a card.

### Motion

| Token | Value | Use |
|---|---|---|
| `--duration-fast` | 150ms | Hover, press, focus |
| `--duration-base` | 200ms | Enter/exit, expand/collapse, toasts |
| `--duration-slow` | 300ms | The rally strip drawing in once (below) |
| `--ease-out` | `cubic-bezier(0.2, 0, 0, 1)` | Everything |

Motion explains a change: something appeared, moved, finished or was selected.
**No infinite loops** except a progress indicator. No page-entrance animation
(the `fade-up` on every page root goes). Never `transition: all`; name the
property. Everything respects `prefers-reduced-motion`.

### Breakpoints

Tailwind's defaults: `sm` 640, `md` 768, `lg` 1024, `xl` 1280. The app's left
rail appears at `lg`, as today.

---

## Layout

- **Signed-in app:** the left rail (220px from `lg`, a drawer below it) holds
  Library, Upload and Collections, plus the account at the bottom. Navigation is
  dimmer than content: `muted` text, the current item in `fg` with a 2px brand
  bar, no background blocks.
- **Signed-out pages (landing, sign in, sign up) are not in the app shell.**
  They get a top bar: the logo on the left, *Sign in* and a primary *Get
  started* on the right. A rail holding only "Log in" and "Sign up" reads as
  an unfinished app.
- **Content width:** 1024px for app pages, 1200px for the game page (the player
  needs it), 1120px for the landing page.
- **One primary action per screen**, a brand-filled `Button` at the right of the
  page header: *Upload* on the Library, *Play top plays* on a game, *Upload* in
  an empty state. Everything else is secondary or ghost.

## Components

`web/src/components/ui/` is the library, and screens use it rather than inline
class strings. It grows to:

- `Button` (primary, secondary, ghost, danger) and `IconButton` (40px hit
  target, a label for screen readers);
- `Input`, `Select`, `Checkbox`;
- `Surface` (replaces the unused `Card`): a `surface` fill, `--radius-surface`,
  no border;
- `Chip`, for filters and action types;
- `Badge`, for status (Ready, Processing, Failed, Demo);
- `Dialog`, `ConfirmDialog` and `Toast`. These replace every `alert()` and
  `confirm()` call; native dialogs are not used;
- `Skeleton`, shaped like the final layout;
- `MediaTile`: a 16:9 thumbnail on `--cf-media`, the duration bottom-right in
  `caption`, and the play score top-left as a `stat` on a rank pill for Top
  plays (a plain `stat` otherwise). The action type and timestamp go below it,
  in `caption`;
- `RallyStrip`, the signature element (below).

**Icons:** lucide only, at three sizes: **16** (default), **14** (inline with
`small`/`caption` text) and **20** (empty states, page headers). Stroke 1.75.
No decorative icons (no `Sparkles`). No emoji anywhere in the UI.

## The signature element: the rally strip

A horizontal bar the length of the whole game, with every kept rally drawn
where it happened. It is the product's core mechanic, a full game turned into
its plays, made visible at a glance. It appears wherever a game does.

- **Data:** each clip's `start_time` and `end_time` against the game's
  `original_duration` (or the last clip's end when that is missing), and its
  `highlight_score` (0–1).
- **Drawing:** each rally is a block at its position, its width its length
  (minimum 2px), and its height its score (the kept range, 0.5–1.0, maps to
  35–100% of the bar). Ordinary rallies are `brand` at 45% opacity; Top plays
  are solid `rank`. The baseline is a 1px `border` line across the full game.
- **Sizes:**
  - game page: 48px tall, full content width, under the player;
  - Library row: 20px tall and 160px wide;
  - landing hero: 64px.
- **Interaction (game page):** hovering a block shows a tooltip, `Spike · 14:23
  · Score 94` (`caption`, tabular). Clicking plays that clip. While a clip
  plays, a 2px `fg` playhead marks its position. Arrow keys move between
  rallies, and Enter plays the focused one.
- **The one deliberate animation:** when a game finishes processing, its
  rallies draw in left to right over `--duration-slow`, once. That is the moment
  the product delivered, so it is the moment that moves.

**Play score** is `round(highlight_score × 100)`. Kept rallies run roughly
50–100. **Top plays** are a game's five highest scores. Classifier confidence
is not a headline number: it appears only in the clip viewer, labelled as label
confidence.

## The hero screen: a game

The primary path is **upload → processing → the game → a clip → download or
share**. The game page is where the product proves itself, so it is the hero.

```
← Library
Varsity vs Lincoln                                   [ Play top plays ]
Sat 21 Sep · Ready ✓

  42          31:04        5
  clips       game length  top plays            ← stat-lg numbers, caption labels

┌──────────────────────────────── player (16:9, media) ─────────────────────────┐
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
 ▁ ▂▅ ▁  █ ▃ ▁▁ ▆  ▂ ▁ █▃ ▁ ▂ ▇ ▁ ▃ ▁▁ ▂ ▅█ ▁   ← rally strip, top plays in rank
───────────────────────────────────────────────

[All] [Spike] [Serve] [Dig] [Set] [Block]   [Player ▾]          Sort: Play score ▾
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│94    │ │91    │ │88    │ │85    │   ← MediaTiles, score top-left, duration bottom-right
│  0:07│ │  0:05│ │  0:09│ │  0:06│
└──────┘ └──────┘ └──────┘ └──────┘
Spike · 14:23  Block · 21:07  …
```

- **Processing:** the strip's place shows the stage name and the ETA (the
  stage labels and ETA we already have), with a thin progress line. Nothing else
  on the page pretends to be ready.
- **Ready:** the rallies draw in (above), the grid fills sorted by play score,
  and *Play top plays* opens the clip viewer on the top play, stepping through
  the other four with next/previous.
- **A clip** opens in the viewer on `--cf-media`, with the strip's playhead
  following it. Download and share live there; a demo clip shows its Demo badge
  instead (CF-565).

## The landing page

Show the product, not a description of it. Real footage depends on rights to the
demo game's footage being recorded (CF-571, #581). Until then the landing shows
**real data from the demo game** (its rally strip and its clip list with play
scores and timestamps) and no thumbnails, rather than mock media.

- **Top bar** (see Layout). No rail.
- **Hero:** a `display` headline that only ClipFarm could say, one `body`
  sentence, and *Get started*. Beside or below it, the demo game's rally strip at
  64px with its top plays listed under it (`stat` score, action, timestamp), and
  with footage once cleared.
- **Below the hero, one section:** the game page itself (the hero screen above)
  at reduced scale, as a real screenshot once footage is cleared, or the clip
  list until then.
- **How it works:** three short lines in `body`, not numbered cards.
- **No** ticker, word-by-word reveal, floating cards, drifting dot grid or
  uppercase section labels.

## Data and copy voice

**Fixtures look like real volleyball:** team names (`Varsity vs Lincoln`,
`Club 16U · Saturday pool play`), game lengths of 25–90 minutes, 20–60 clips per
game, scores between 52 and 97, clip lengths of 4–12 seconds, and timestamps
spread across the game. Never "Test game", lorem ipsum or round numbers.

**Voice: courtside and plain.** Short, specific, in the second person, with no
hype and no exclamation marks.

- **One verb per action:** *Upload* (never "New game", "Upload now", "Upload &
  process"); *Sign in* / *Sign up* / *Sign out* (never "Log in").
- **Sentence case everywhere**, including buttons, labels and headers.
- **Numbers carry their noun:** *Score 94*, *42 clips*, *31 min*, *Label
  confidence 94%*. Never a bare "94%".
- **Headlines say what only ClipFarm does** (every rally found and ranked), not
  what any tool does ("automatically", "AI-powered", "highlight reel").
- **Errors say what happened and what to do:** *The upload stopped at 62%.
  Check your connection and try again; the part already sent is kept.*
- **Confirmations name the thing:** *Delete "Varsity vs Lincoln" and its 42
  clips?*
- Toggles show the choice, not the current state.

## Don'ts for ClipFarm

- **No mock or illustrative media.** If there is no footage to show, show the
  data (the strip, the scores), never gradient placeholder cards.
- **No decorative motion:** no tickers, floating cards, drifting grids or
  staggered reveals.
- **No text under 12px, and no uppercase tracked labels.**
- **No information in `subtle`.**
- **No colour outside the tokens**, and no per-action colours.
- **Don't lead a clip with classifier confidence.** The play score leads.
- **No native `alert()` or `confirm()`.**
- **No borders around surfaces, no cards inside cards, no mixed radii.**
- **Video never sits on cream.**
- **No signed-out page inside the app shell.**
- **No second display face**, no Barlow, no Oswald, and no italic serif accent
  word.

---

## Getting from here to there

The current screens predate this file. These cards implement it, in order;
each stands alone and leaves the app shippable.

1. **Tokens** (CF-577, #592): the colour roles, type roles, display face, radius,
   elevation and motion tokens; light by default; the two hard-coded colours
   that bypass the tokens today.
2. **Components** (CF-578, #593): `Surface`, `Input`, `Chip`, `IconButton`,
   `Dialog`/`ConfirmDialog`/`Toast`, replacing all native dialogs.
3. **Clips lead with the play score** (CF-579, #594): `MediaTile`, neutral action
   chips, sort by score, Top plays, and confidence demoted.
4. **The rally strip** (CF-580, #595) on the game page, with the hero layout and
   *Play top plays*.
5. **Library rows** (CF-581, #596): a thumbnail and a mini rally strip per game.
6. **Landing and auth pages** (CF-582, #597): out of the app shell, rebuilt as above.
7. **Every other screen** (CF-583, #598): moved onto the type roles and tokens, plus
   the copy pass.
