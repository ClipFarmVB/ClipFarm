/**
 * Is the viewport wide enough that the sidebar is a column rather than an
 * overlay? (CF-60)
 *
 * The responsive layout is CSS, and stays that way wherever CSS can express
 * it — the scroll lock, for instance, puts its breakpoint in the stylesheet
 * precisely so no listener has to stay in step with Tailwind's. This hook
 * exists for the one thing that has no CSS form: `inert`. A closed off-canvas
 * drawer must be inert so its links leave the tab order and the accessibility
 * tree, and the same element must stay fully interactive once it is the
 * desktop column — that is an attribute decision, and only JS can make it.
 *
 * The server snapshot is `true` deliberately. Guessing "mobile" on the server
 * would ship HTML with the sidebar marked `inert` on a desktop, leaving it
 * dead to clicks until hydration; guessing "desktop" costs at worst a closed
 * drawer that is briefly tabbable on a phone before hydration corrects it.
 *
 * Keep the query in step with the breakpoint in globals.css.
 *
 * Heads up when verifying this in a driven browser: resizing through CDP
 * updates `matchMedia().matches` but never fires `change`, so the
 * subscription below looks dead when it is fine. Reload at the target width
 * and read the value instead. See "Verifying responsive work in a headless
 * browser" in web/AGENTS.md.
 */
import { useSyncExternalStore } from "react";

/** Tailwind's `lg`. The same breakpoint the drawer's CSS uses. */
const LG_QUERY = "(min-width: 64rem)";

function subscribe(onChange: () => void) {
  const mql = window.matchMedia(LG_QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

export function useIsDesktopLayout(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(LG_QUERY).matches,
    () => true
  );
}
