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
 * Locks are reference-counted per class, so overlays nest: the collection
 * picker opening over the clip modal takes a second lock, and releasing the
 * inner one leaves the page locked for the outer. Counting is what makes that
 * true — bare add/remove would only work for as long as no two callers happen
 * to share a class, which is a trap rather than a guarantee.
 */
import { useEffect } from "react";

const holders = new Map<string, number>();

function acquire(className: string) {
  const next = (holders.get(className) ?? 0) + 1;
  holders.set(className, next);
  if (next === 1) document.body.classList.add(className);
}

function release(className: string) {
  const next = (holders.get(className) ?? 1) - 1;
  if (next > 0) {
    holders.set(className, next);
    return;
  }
  holders.delete(className);
  document.body.classList.remove(className);
}

function useBodyClass(className: string, active: boolean) {
  useEffect(() => {
    if (!active) return;
    acquire(className);
    return () => release(className);
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
