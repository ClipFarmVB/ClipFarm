/**
 * Hold the page still behind an overlay (CF-60).
 *
 * The clip modal and the mobile nav drawer both need it, so it lives here
 * rather than being written out twice and drifting.
 *
 * Applied as a class rather than an inline style so the *stylesheet* decides
 * when the lock takes effect. The drawer needs that: from `lg` the sidebar is
 * a permanent column, not an overlay, and a lock that outlived the breakpoint
 * would leave the page unscrollable with no backdrop on screen to explain why.
 * Rotating a tablet from portrait to landscape does exactly that, and it never
 * changes the drawer's own state — so there is nothing for React to react to.
 * Expressing the breakpoint in CSS means crossing it releases the lock with no
 * resize listener and no JS media query to keep in step with the Tailwind one.
 *
 * Classes also nest safely where a single inline style does not: two overlays
 * open at once (the collection picker over the clip modal) each add and remove
 * only their own, so the inner one's cleanup cannot unlock the page while the
 * outer one is still up.
 */
import { useEffect } from "react";

function useBodyClass(className: string, active: boolean) {
  useEffect(() => {
    if (!active) return;
    document.body.classList.add(className);
    return () => document.body.classList.remove(className);
  }, [className, active]);
}

/** Lock at every width — for an overlay that covers the page everywhere. */
export function useBodyScrollLock(active: boolean) {
  useBodyClass("cf-lock-scroll", active);
}

/**
 * Lock only below `lg`, where the nav drawer is an overlay. Above it the
 * sidebar is an ordinary column and the page must keep scrolling.
 */
export function useBodyScrollLockBelowLg(active: boolean) {
  useBodyClass("cf-lock-scroll-below-lg", active);
}
