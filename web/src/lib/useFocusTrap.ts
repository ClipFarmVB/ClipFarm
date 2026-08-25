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

// Tab order inside an overlay. Deliberately the plain set — but `video[controls]`
// earns its place: ClipModal's whole subject is one, browsers make it focusable,
// and leaving it out puts the last real stop *before* it. A forward Tab from the
// player would then see focus inside the container and not at `last`, decline to
// wrap, and let the browser walk on into the grid behind the backdrop.
export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), video[controls], audio[controls], ' +
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
 */
export function trapTabWithin(container: HTMLElement, e: KeyboardEvent): boolean {
  const items = container.querySelectorAll<HTMLElement>(FOCUSABLE);
  if (items.length === 0) return false;
  const first = items[0];
  const last = items[items.length - 1];
  const active = container.ownerDocument.activeElement;

  const outside = !container.contains(active);
  const leaving = e.shiftKey ? active === first : active === last;
  if (!outside && !leaving) return false;

  e.preventDefault();
  (e.shiftKey ? last : first).focus();
  return true;
}


/**
 * Restore focus to `el`, if that would actually land somewhere.
 *
 * A detached or hidden element cannot take focus, and calling focus() on one
 * drops the caller to `<body>` — worse than leaving focus alone. The Sidebar
 * case that found this: closing the drawer by crossing into the desktop layout
 * runs the same cleanup, and the hamburger is `display: none` there.
 *
 * **`checkVisibility()` rather than Sidebar's `offsetParent === null`.** That
 * test was correct for the hamburger and wrong in general: `offsetParent` is
 * also null for a `position: fixed` element, so lifting it unchanged would have
 * refused to restore focus to any fixed-position trigger — and this hook now
 * restores to *whatever* held focus, not to one known button. Guarded with a
 * capability check because it is a layout API: environments without layout
 * (jsdom, and so this project's tests) do not implement it, and there the
 * connected check is all that runs.
 */
export function restoreFocusTo(el: Element | null): void {
  const target = el as HTMLElement | null;
  if (!target?.isConnected) return;
  if (typeof target.checkVisibility === "function" && !target.checkVisibility()) return;
  target.focus();
}


export interface FocusTrapOptions {
  /** Focused on activation. Defaults to the first focusable in the container. */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** Called on Escape. Omit to leave Escape to the caller. */
  onEscape?: () => void;
}

/**
 * Trap focus within `containerRef` while `active`.
 *
 * On deactivation, focus returns to whatever held it when the trap engaged —
 * captured rather than passed in, so a caller does not have to thread a ref
 * back to its own trigger. That is what gives ClipModal "closing returns focus
 * to the clip card that opened it" without ClipModal knowing which card that
 * was.
 */
export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  active: boolean,
  options: FocusTrapOptions = {},
): void {
  const { initialFocusRef, onEscape } = options;

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    const previouslyFocused = container.ownerDocument.activeElement;

    const initial =
      initialFocusRef?.current ??
      container.querySelector<HTMLElement>(FOCUSABLE);
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
      restoreFocusTo(previouslyFocused);
    };
    // containerRef and initialFocusRef are refs; onEscape is read through the
    // closure and callers pass a stable handler.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
}
