"use client";

import { useEffect, type RefObject } from "react";

/**
 * Everything an overlay covering the page owes the keyboard (CF-227).
 *
 * Lifted out of Sidebar, which grew it for CF-60 and was the only caller: focus
 * moves in on open and back to whatever opened it on close, and Tab stays
 * inside rather than walking into the page behind the backdrop. ClipModal and
 * CollectionPickerModal are overlays in the same position and had none of it.
 *
 * One implementation rather than three, because the interesting part is a
 * one-line condition that was already got wrong once — see `trapTabWithin`.
 */

// Tab order inside an overlay. Deliberately the plain set — links, buttons and
// form controls. `input[type=hidden]` is excluded because it matches
// `input:not([disabled])` but cannot take focus, and a hidden input becoming
// `first` or `last` would make the wrap target unfocusable.
//
// `video[controls]` is NOT here, and that is deliberate: browsers make it
// focusable, but listing it makes it a wrap boundary, and Tab from a video is
// how a keyboard user reaches the seek bar and volume in its shadow controls.
// `nextFocusableAfter` below handles it instead — it is focusable-but-unlisted,
// which is exactly the case that needed solving generally.
export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
  'select:not([disabled]), textarea:not([disabled]), ' +
  '[tabindex]:not([tabindex="-1"])';


/**
 * Keep Tab inside `container`. Returns true if the event was handled.
 *
 * Exported and container-in/boolean-out so the wrap rule can be tested against
 * a real DOM without a React renderer — it is the part worth pinning.
 *
 * **Wraps in *both* directions when focus is outside the container**, which is
 * the bug CF-60 hit and the reason this is shared rather than copied. Focus
 * lands outside by clicking chrome inside the overlay that cannot hold focus —
 * a heading, the padding — which leaves it on `<body>`. Guarding only the
 * shift branch let a forward Tab from there fall through to whatever sits
 * behind the backdrop and walk on into the page.
 *
 * **Inside the container, the boundary is document order rather than the
 * FOCUSABLE list.** An element can be focusable without matching that selector
 * — `<video controls>` is the one ClipModal cares about — and treating the last
 * *listed* element as the boundary both traps too early (blocking the video's
 * own controls) and too late (letting Tab off the end of an unlisted trailing
 * element). Asking "is there anything focusable after this in the container"
 * gets both right, and generalises to whatever the next overlay puts in.
 */
export function trapTabWithin(container: HTMLElement, e: KeyboardEvent): boolean {
  const items = container.querySelectorAll<HTMLElement>(FOCUSABLE);
  if (items.length === 0) return false;
  const first = items[0];
  const last = items[items.length - 1];
  const active = container.ownerDocument.activeElement;

  if (!container.contains(active)) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
    return true;
  }

  const leaving = e.shiftKey
    ? !previousFocusableBefore(container, active as HTMLElement)
    : !nextFocusableAfter(container, active as HTMLElement);
  if (!leaving) return false;

  e.preventDefault();
  (e.shiftKey ? last : first).focus();
  return true;
}


/** The next FOCUSABLE after `el` in `container`, in document order. */
export function nextFocusableAfter(
  container: HTMLElement, el: HTMLElement,
): HTMLElement | null {
  const items = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)];
  return items.find(
    (n) => el.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING,
  ) ?? null;
}


/** The previous FOCUSABLE before `el` in `container`, in document order. */
export function previousFocusableBefore(
  container: HTMLElement, el: HTMLElement,
): HTMLElement | null {
  const items = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)];
  return [...items].reverse().find(
    (n) => el.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_PRECEDING,
  ) ?? null;
}


/**
 * Restore focus to `el`, if that would actually land somewhere.
 *
 * A detached or unrendered element cannot take focus, and calling focus() on
 * one drops the caller to `<body>` — worse than leaving focus alone. The
 * Sidebar case that found this: closing the drawer by crossing into the desktop
 * layout runs the same cleanup, and the hamburger is `display: none` there.
 *
 * **`getClientRects()` rather than `offsetParent` or `checkVisibility()`.**
 * `offsetParent` was Sidebar's test and is correct for a hamburger but wrong in
 * general — it is also null for a `position: fixed` element, and this restores
 * to whatever held focus rather than to one known button. `checkVisibility()`
 * is the modern answer but needs a capability check, and where it is missing
 * (Safari before 17.4, Firefox before 125) the guard silently passes and the
 * display:none bug comes back. `getClientRects()` is old, universal, empty for
 * an unrendered element, and — being an ordinary method — stubbable, so both
 * branches are testable.
 */
export function restoreFocusTo(el: Element | null): void {
  const target = el as HTMLElement | null;
  if (!target?.isConnected) return;
  if (target.getClientRects().length === 0) return;
  target.focus();
}


export interface FocusTrapOptions {
  /**
   * Focused on activation. A getter rather than a ref so a caller can hand back
   * any element type without a cast — and so a modal can choose something that
   * is safe to press: the default is the first focusable, which in ClipModal is
   * "Copy link", where the first Space after opening would fire a request and
   * overwrite the clipboard.
   */
  initialFocus?: () => HTMLElement | null | undefined;
  /**
   * Focused on deactivation, in preference to whatever held focus when the trap
   * engaged. Needed where the capture is unreliable: WebKit does not focus a
   * button on click or tap, so a mobile drawer opened by tapping its hamburger
   * captures `<body>` and would restore focus to nothing.
   */
  restoreFocusRef?: RefObject<HTMLElement | null>;
  /** Called on Escape. Omit to leave Escape to the caller. */
  onEscape?: () => void;
}


export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  active: boolean,
  options: FocusTrapOptions = {},
): void {
  const { initialFocus, restoreFocusRef, onEscape } = options;

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    // Captured now, not read in the cleanup: by the time the cleanup runs the
    // ref may point elsewhere, and eslint is right to say so. At activation the
    // trigger is rendered and current, which is the value we want anyway.
    const restoreTarget =
      restoreFocusRef?.current ?? container.ownerDocument.activeElement;

    const initial =
      initialFocus?.() ?? container.querySelector<HTMLElement>(FOCUSABLE);
    initial?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && onEscape) { onEscape(); return; }
      if (e.key !== "Tab") return;
      const el = containerRef.current;
      if (el) trapTabWithin(el, e);
    };

    const win = container.ownerDocument.defaultView ?? window;
    win.addEventListener("keydown", onKey);
    return () => {
      win.removeEventListener("keydown", onKey);
      restoreFocusTo(restoreTarget);
    };
    // containerRef and initialFocusRef are refs; onEscape is read through the
    // closure and callers pass a stable handler.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
}
