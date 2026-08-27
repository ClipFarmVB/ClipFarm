"use client";

import { useEffect, useRef, type RefObject } from "react";

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
// `input:not([disabled])` but cannot take focus, and a hidden input becoming a
// wrap target would send focus nowhere.
//
// `video[controls]` IS here. It has to be: the wrap targets come from this list,
// so an unlisted focusable is in neither Tab cycle — in a single-clip modal
// (copy, close, video, no prev/next) Tab from Close wrapped straight back to
// Copy and the player was unreachable in either direction.
//
// **Known limitation, not solved here.** A UA media control lives in a closed
// shadow root, so `document.activeElement` stays the `<video>` host however far
// Tab has walked inside it. Nothing in JavaScript can tell "still in the seek
// bar" from "done with the player", so a trap that keeps focus in the overlay
// necessarily also keeps it out of those controls. Letting Tab through instead
// would leak focus into the grid behind the backdrop, which is worse. Real
// remedies are our own controls or an explicit escape hatch — both larger than
// this card.
export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
  'select:not([disabled]), textarea:not([disabled]), video[controls], ' +
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
 * **Inside the container, the boundary is document order rather than position
 * in the FOCUSABLE list.** For anything the selector matches the two agree —
 * nothing follows the last listed element, so it reduces to `active === last`.
 * `<video controls>` is *in* the list (see FOCUSABLE) and is not the case this
 * buys anything for.
 *
 * What it buys is an element holding focus that the selector does **not**
 * match: ClipModal's `tabindex="-1"` card, focused on open. An index-based test
 * says "not first, not last, carry on" and shift-Tab from the card falls
 * straight out of the overlay; asking "is anything focusable before this in the
 * container" returns null and wraps to the last control, which is right. It
 * also generalises to whatever the next overlay puts in.
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
  // `items` is already a fresh array from the spread above, so reversing it in
  // place is safe — the NodeList it came from is untouched.
  const items = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)];
  return items.reverse().find(
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

  // The effect below depends only on `active`, so everything it closes over is
  // captured once. That silently broke CollectionPickerModal: it passes
  // `creating ? undefined : onClose`, and the handler kept the value from the
  // first render forever — Escape closed the picker while its "new collection"
  // field was open, the exact thing that option existed to prevent. A ref
  // refreshed every render is read at keypress time instead, so the contract is
  // "pass whatever you like, it will be current" rather than an unenforced
  // "callers pass a stable handler".
  const onEscapeRef = useRef(onEscape);
  // In an effect rather than during render: assigning to a ref while rendering
  // is a side effect React is entitled to discard or repeat under concurrent
  // rendering. This runs after every commit, which is soon enough — the value
  // is only read on a keypress.
  useEffect(() => { onEscapeRef.current = onEscape; });

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) {
      // The effect depends only on `active`, so there is no re-run to recover
      // on: a null container here disables the trap for as long as the overlay
      // is open. Refs attach before effects fire, so this means the ref was
      // never wired to a rendered element — a caller bug, and a silent one,
      // because a trap that does nothing looks exactly like a trap.
      if (process.env.NODE_ENV !== "production") {
        console.warn(
          "useFocusTrap: containerRef is null while active; the trap is disabled. " +
            "Attach the ref to the element rendered for this overlay.",
        );
      }
      return;
    }

    // Captured now, not read in the cleanup: by the time the cleanup runs the
    // ref may point elsewhere, and eslint is right to say so. At activation the
    // trigger is rendered and current, which is the value we want anyway.
    const restoreTarget =
      restoreFocusRef?.current ?? container.ownerDocument.activeElement;

    const initial =
      initialFocus?.() ?? container.querySelector<HTMLElement>(FOCUSABLE);
    initial?.focus();

    const onKey = (e: KeyboardEvent) => {
      const escape = onEscapeRef.current;
      if (e.key === "Escape" && escape) {
        // Claim the key. Without this the UA's own Escape runs alongside ours:
        // Firefox reverts the text field being edited, and the browser-level
        // binding — leaving fullscreen is the usual one — fires too, so one
        // press does two things the user asked for once.
        e.preventDefault();
        escape();
        return;
      }
      if (e.key !== "Tab") return;
      const el = containerRef.current;
      if (el) trapTabWithin(el, e);
    };

    // Capture on the document, not bubble on the window, and for a *shared*
    // hook the difference is load-bearing. Bubbling puts the trap last, behind
    // every handler between the target and the window, so a single
    // `e.stopPropagation()` in any child's onKeyDown — a common reflex in a
    // text field — silently turns off both Tab trapping and Escape, and
    // nothing shows it until someone tries the keyboard. Capture runs ahead of
    // all of them: no overlay can disable the trap by accident, and callers do
    // not have to reason about listener ordering to stay correct.
    //
    // Still one listener per active trap, so "at most one overlay at a time"
    // remains load-bearing and enforced by nothing — two live traps would each
    // handle the same Escape and the same Tab. It holds today because the three
    // callers cannot be mounted together, but that is a property of the call
    // graph rather than of this hook, so nesting is NOT supported: CF-282
    // (#328).
    //
    // Capture does not help with that. Every trap binds to the same node, and
    // same-node listeners run in registration order within a phase, so the
    // outer trap fires first — from mount order, exactly as it did on the
    // window. And outer-first is the wrong end to start from: a nested trap
    // wants the INNERMOST one to claim the key, so the guard has to be the
    // outer trap detecting a live inner trap and standing down, not simply
    // whoever is called first handling it.
    const doc = container.ownerDocument;
    doc.addEventListener("keydown", onKey, true);
    return () => {
      doc.removeEventListener("keydown", onKey, true);
      restoreFocusTo(restoreTarget);
    };
    // containerRef, initialFocus and restoreFocusRef are read at activation;
    // onEscape is read through a ref at keypress time, so none of them belongs
    // in the dependency array — re-running the effect would re-steal focus.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
}
