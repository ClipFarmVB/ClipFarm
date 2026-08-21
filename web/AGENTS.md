<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->


# Verifying responsive work in a headless browser

Media-query behaviour does **not** fully emulate. A driven browser resized
through CDP (`Emulation.setDeviceMetricsOverride` / `setEmulatedMedia` — what
the Claude Code browser tools and most Playwright/Puppeteer setups use) will
hand back confident, wrong answers in two specific ways. Both cost real
debugging time on CF-60:

1. **`matchMedia(q).matches` flips without dispatching `change`.** Resize
   across a breakpoint and the *value* updates, but no `change` event fires —
   so anything subscribed to it (`useSyncExternalStore`, a `useMediaQuery`,
   a plain listener) never re-renders. Code that is correct in a real browser
   looks broken.

2. **CSS re-evaluates `width` queries but not `hover`/`pointer` ones.**
   `@media (hover: none)` and `@media (pointer: coarse)` keep the value they
   had at load, even though `matchMedia` in JS reports the new one. So JS and
   CSS disagree, and a touch-only rule appears not to apply.

**Reload after every resize, before you measure.** A fresh load evaluates
everything against the current emulated viewport, and both quirks disappear.
Nothing in the app needs a reload — this is purely how you take a reading.

The corollary is a design rule, not just a testing one: **put a breakpoint in
CSS wherever CSS can express it.** A stylesheet breakpoint is testable here,
needs no listener, and cannot drift from Tailwind's. Reach for a JS media
query only for things CSS has no form for — `inert` is the example in the
codebase, and `useIsDesktopLayout` exists for that one job.

Measuring beats screenshotting when the pane cannot composite: check
`document.documentElement.scrollWidth` against `innerWidth` for overflow, and
`getBoundingClientRect()` for tap-target sizes. Note that a CSS transition
mid-flight reports its *pre-transition* geometry while the page is not
painting — set `style.transition = "none"`, force a reflow, then read.
