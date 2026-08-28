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


/**
 * Which live trap owns the keyboard, when more than one is mounted (CF-282).
 *
 * Every trap binds its own listener to the same node, so with two of them
 * active both see the same Tab and the same Escape. Same-node listeners run in
 * registration order, which makes the OUTER trap run first — the wrong end.
 * The innermost overlay is the one the user is looking at and the one whose
 * Escape should close and whose bounds Tab should respect.
 *
 * A stack settles it with no cooperation from callers: a trap pushes a token
 * while active and handles keys only while that token is on top. The outer trap
 * does not need to know an inner one exists; it just stops being on top.
 *
 * Plain functions over a module-level array, exported for the same reason
 * `trapTabWithin` is: the ordering rule is the part worth pinning, and this way
 * it is pinned without mounting anything.
 */
const trapStack: object[] = [];

/** Register `token` as the innermost trap. Idempotent. */
export function pushTrap(token: object): void {
  // Idempotent because an effect that re-runs without its cleanup having run
  // would otherwise leave the same token twice, and the second copy would keep
  // an unmounted trap on top forever.
  if (!trapStack.includes(token)) trapStack.push(token);
}

/** Unregister `token`, wherever it sits. */
export function removeTrap(token: object): void {
  // By index rather than `pop()`: React does not promise that cleanups run in
  // reverse mount order, so an outer trap can unmount first. Popping blindly
  // would then remove the inner trap's token and hand the keyboard to a trap
  // that is going away.
  const i = trapStack.lastIndexOf(token);
  if (i !== -1) trapStack.splice(i, 1);
}

/** Is `token` the innermost live trap? False for an unregistered token. */
export function isInnermostTrap(token: object): boolean {
  return trapStack.length > 0 && trapStack[trapStack.length - 1] === token;
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
    // Only the activeElement half is captured now. It has to be: by cleanup
    // the focus has already moved. `restoreFocusRef` is read in the cleanup
    // instead, because a ref object's identity is stable and its `.current` is
    // the whole reason callers pass one — snapshotting it here would hand back
    // the trigger as it was at activation, which is the staleness the option
    // exists to avoid.
    const capturedActive = container.ownerDocument.activeElement;

    const initial =
      initialFocus?.() ?? container.querySelector<HTMLElement>(FOCUSABLE);
    initial?.focus();

    // Identity only — never read, only compared. An object literal is the
    // cheapest value that is equal to nothing else.
    const token = {};
    pushTrap(token);

    const onKey = (e: KeyboardEvent) => {
      // Innermost trap wins. Every live trap gets this event; all but the top
      // of the stack decline it, so a nested overlay's Escape closes the
      // nested overlay and its Tab stays inside the nested bounds, rather than
      // both traps acting on one keypress.
      if (!isInnermostTrap(token)) return;
      // An IME is mid-composition: Escape cancels the candidate list and Tab
      // moves between candidates, and both belong to the IME rather than to
      // this overlay. Without this, one Escape while composing a collection
      // name reaches `cancelCreating` and discards everything typed so far —
      // the user asked to dismiss a candidate and lost the field.
      if (e.isComposing) return;
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
    // Still one listener per active trap, all on the same node, so every live
    // trap sees every key. That used to make "at most one overlay at a time" a
    // load-bearing invariant enforced by nothing — true only because the three
    // callers could not be mounted together, which is a property of the call
    // graph rather than of this hook. The trap stack above enforces it here
    // instead: all but the innermost decline, so nesting is now supported
    // rather than merely unlikely. CF-282 (#328) is closed by this.
    //
    // Note that capture phase contributes nothing to that. Same-node listeners
    // run in registration order within a phase, so the outer trap runs first
    // either way — and outer-first is the wrong end, which is precisely why the
    // ordering had to be decided by the stack rather than by the DOM. What
    // capture buys is the stopPropagation immunity described above, and only
    // that.
    const doc = container.ownerDocument;
    doc.addEventListener("keydown", onKey, true);
    return () => {
      // Off the stack first, so the trap below becomes innermost immediately
      // rather than after the focus restore below has run.
      removeTrap(token);
      doc.removeEventListener("keydown", onKey, true);
      // eslint's generic advice — copy the ref into a variable inside the
      // effect — is exactly what this stopped doing, and would reinstate the
      // staleness. Reading late is the point: the caller passes a ref so that
      // the trigger is whatever it is NOW. The hazard the rule guards against,
      // a node that has since unmounted, is already handled — restoreFocusTo
      // declines anything with no client rects.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      restoreFocusTo(restoreFocusRef?.current ?? capturedActive);
    };
    // containerRef, initialFocus and restoreFocusRef are read at activation;
    // onEscape is read through a ref at keypress time, so none of them belongs
    // in the dependency array — re-running the effect would re-steal focus.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
}
