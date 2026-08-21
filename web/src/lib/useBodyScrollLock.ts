/**
 * Hold the page still behind an overlay (CF-60).
 *
 * The clip modal and the mobile nav drawer both need it, so it lives here
 * rather than being written out twice and drifting. Counting lives in
 * `classLock`, where it is testable without a DOM.
 *
 * Applied as a class rather than an inline style so the *stylesheet* decides
 * when the lock takes effect. The drawer needs that: from `lg` the sidebar is
 * a permanent column, not an overlay, and a lock that outlived the breakpoint
 * would leave the page unscrollable with no backdrop on screen to explain why.
 * Rotating a tablet from portrait to landscape does exactly that, and it never
 * changes the drawer's own state — so there is nothing for React to react to.
 *
 * ── Two locks, because `overflow: hidden` is not the same everywhere ──
 *
 * `overflow: hidden` on <body> is an unreliable lock on iOS Safari: it stops
 * programmatic scrolling but not touch panning, so the page still rubber-bands
 * under the overlay. Pinning the body with `position: fixed` is what actually
 * holds it — at the cost of collapsing the scroll offset to zero, which then
 * has to be saved and restored by hand.
 *
 * So the two variants differ deliberately:
 *
 *   useBodyScrollLock       pins. It backs the clip modal, a full-screen video
 *                           sheet on a phone, where panning the grid behind it
 *                           is the worst of the two failure modes. Its release
 *                           always runs through this hook, so the offset can be
 *                           restored.
 *
 *   useBodyScrollLockBelowLg does not pin. It backs the nav drawer, whose lock
 *                           is released by the *stylesheet* when the viewport
 *                           crosses `lg` — no JS runs at that moment, so there
 *                           would be nobody to restore a collapsed offset and
 *                           rotating a tablet would jump the page to the top.
 *                           Keeping the CSS-driven release is worth more here
 *                           than closing an iOS rubber-band behind a backdrop.
 *
 * The pinning half is unverified on real iOS — see web/AGENTS.md; the
 * save/restore of the scroll offset is verifiable and is covered in-browser.
 */
import { useEffect } from "react";

import { createClassLock } from "./classLock";

const lock = createClassLock(() => document.body.classList);

/** Offset captured when the body was pinned, restored when it is released. */
let pinnedAt = 0;

const pin = () => {
  pinnedAt = window.scrollY;
  // `position: fixed` comes from the class; this is the part that keeps the
  // viewport looking at the same place while the body is out of flow.
  document.body.style.top = `-${pinnedAt}px`;
};

const unpin = () => {
  document.body.style.top = "";
  window.scrollTo(0, pinnedAt);
};

function useBodyClass(className: string, active: boolean, pinBody: boolean) {
  useEffect(() => {
    if (!active) return;
    const hooks = pinBody ? { onFirstAcquire: pin, onLastRelease: unpin } : undefined;
    lock.acquire(className, hooks);
    return () => lock.release(className, hooks);
  }, [className, active, pinBody]);
}

/**
 * Lock at every width, pinning the body — for an overlay that covers the page
 * everywhere and is always dismissed through React.
 */
export function useBodyScrollLock(active: boolean) {
  useBodyClass("cf-lock-scroll", active, true);
}

/**
 * Lock only below `lg`, where the nav drawer is an overlay. Above it the
 * sidebar is an ordinary column and the page must keep scrolling — which the
 * stylesheet handles on its own, so this variant must not pin.
 */
export function useBodyScrollLockBelowLg(active: boolean) {
  useBodyClass("cf-lock-scroll-below-lg", active, false);
}
