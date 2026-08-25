// @vitest-environment jsdom
//
// The first test in this project that needs a DOM. Opted in per-file rather
// than flipping vitest.config.mts to jsdom globally: every other suite here is
// pure logic and has no reason to pay for a document.
//
// No React renderer is involved. useFocusTrap is a thin useEffect around the
// two functions below, and those are where the behaviour that has already been
// got wrong once lives — so they are what gets pinned.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  FOCUSABLE,
  nextFocusableAfter,
  previousFocusableBefore,
  restoreFocusTo,
  trapTabWithin,
} from "@/lib/useFocusTrap";

/** jsdom has no layout, so getClientRects() is always empty. Make one "rendered". */
function render(el: HTMLElement): HTMLElement {
  el.getClientRects = () => [{}] as unknown as DOMRectList;
  return el;
}

function overlay(html: string): HTMLElement {
  document.body.innerHTML = `<button id="behind">behind</button><div id="overlay">${html}</div>`;
  return document.getElementById("overlay")!;
}

function tab(shiftKey = false): KeyboardEvent {
  return new KeyboardEvent("keydown", { key: "Tab", shiftKey, cancelable: true });
}

/**
 * jsdom will not focus a `<video>`, so the only way to exercise the player
 * branch is to say what `activeElement` is. `defineProperty` puts an *own*
 * property on `document` that shadows `Document.prototype`'s getter, and it
 * stays there for the rest of the file unless it is removed — a stale, detached
 * element then answers every later `document.activeElement` read. Pair every
 * call with the `afterEach` below rather than defining the property inline.
 */
function setActiveElement(el: Element): void {
  Object.defineProperty(document, "activeElement", {
    value: el, configurable: true,
  });
}

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  // Deleting the own property unshadows the real getter. `delete` rather than
  // re-defining it: Document.prototype's is an accessor, and defining a value
  // back over it would leave the shadow in place.
  Reflect.deleteProperty(document, "activeElement");
});


describe("FOCUSABLE", () => {
  it("excludes a hidden input, which cannot take focus", () => {
    // It matches `input:not([disabled])`, so without the extra clause a hidden
    // input could become `first` or `last` and the wrap target would be
    // unfocusable.
    const el = overlay(`<input type="hidden" /><button id="a">a</button>`);
    const tags = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].map((n) => n.tagName);
    expect(tags).toEqual(["BUTTON"]);
  });

  it("skips disabled controls and tabindex=-1", () => {
    const el = overlay(`
      <a href="#a">a</a>
      <button disabled>no</button>
      <input disabled />
      <div tabindex="-1">no</div>
      <button id="b">b</button>
    `);
    const tags = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].map((n) => n.tagName);
    expect(tags).toEqual(["A", "BUTTON"]);
  });
});


describe("trapTabWithin", () => {
  it("wraps forward from the last element to the first", () => {
    const el = overlay(`<button id="first">1</button><button id="last">2</button>`);
    document.getElementById("last")!.focus();

    const e = tab();
    expect(trapTabWithin(el, e)).toBe(true);
    expect(e.defaultPrevented).toBe(true);
    expect(document.activeElement?.id).toBe("first");
  });

  it("wraps backward from the first element to the last", () => {
    const el = overlay(`<button id="first">1</button><button id="last">2</button>`);
    document.getElementById("first")!.focus();

    const e = tab(true);
    expect(trapTabWithin(el, e)).toBe(true);
    expect(document.activeElement?.id).toBe("last");
  });

  it("leaves an ordinary Tab in the middle alone", () => {
    const el = overlay(`<button id="a">1</button><button id="b">2</button><button id="c">3</button>`);
    document.getElementById("b")!.focus();

    const e = tab();
    expect(trapTabWithin(el, e)).toBe(false);
    expect(e.defaultPrevented).toBe(false);
    expect(document.activeElement?.id).toBe("b");
  });

  // The CF-60 bug, in both directions. Focus lands on <body> by clicking chrome
  // inside the overlay that cannot hold focus — a heading, the padding.
  it("wraps forward when focus is outside the overlay entirely", () => {
    const el = overlay(`<button id="first">1</button><button id="last">2</button>`);
    (document.activeElement as HTMLElement | null)?.blur();
    expect(el.contains(document.activeElement)).toBe(false);

    const e = tab();
    expect(trapTabWithin(el, e)).toBe(true);
    expect(document.activeElement?.id).toBe("first");
  });

  it("wraps backward when focus is outside the overlay entirely", () => {
    const el = overlay(`<button id="first">1</button><button id="last">2</button>`);
    (document.activeElement as HTMLElement | null)?.blur();

    const e = tab(true);
    expect(trapTabWithin(el, e)).toBe(true);
    expect(document.activeElement?.id).toBe("last");
  });

  it("never lets focus reach a control behind the overlay", () => {
    const el = overlay(`<button id="first">1</button><button id="last">2</button>`);
    document.getElementById("behind")!.focus();

    trapTabWithin(el, tab());
    expect(document.activeElement?.id).not.toBe("behind");
    expect(el.contains(document.activeElement)).toBe(true);
  });

  it("does nothing when the overlay holds nothing focusable", () => {
    // Not merely "returns false": preventDefault here would strand the user in
    // an overlay Tab cannot leave and Tab cannot move within.
    const el = overlay(`<h2>Workspace</h2><p>no controls</p>`);
    const e = tab();
    expect(trapTabWithin(el, e)).toBe(false);
    expect(e.defaultPrevented).toBe(false);
  });

  it("treats a single focusable element as both ends", () => {
    const el = overlay(`<button id="only">x</button>`);
    document.getElementById("only")!.focus();

    expect(trapTabWithin(el, tab())).toBe(true);
    expect(document.activeElement?.id).toBe("only");
    expect(trapTabWithin(el, tab(true))).toBe(true);
    expect(document.activeElement?.id).toBe("only");
  });
});


describe("restoreFocusTo", () => {
  it("restores focus to a connected, rendered element", () => {
    overlay(`<button id="inside">x</button>`);
    const behind = render(document.getElementById("behind")!);
    document.getElementById("inside")!.focus();

    restoreFocusTo(behind);
    expect(document.activeElement?.id).toBe("behind");
  });

  it("leaves focus alone when the element is not rendered", () => {
    // The Sidebar case: closing the drawer by crossing into the desktop layout
    // runs the same cleanup while the hamburger is display:none. getClientRects
    // is empty for an unrendered element — and being an ordinary method rather
    // than a capability-gated one, it is stubbable, so this branch is testable
    // where `checkVisibility()` and `offsetParent` were not.
    overlay(`<button id="inside">x</button>`);
    const behind = document.getElementById("behind")!;  // not rendered
    document.getElementById("inside")!.focus();

    restoreFocusTo(behind);
    expect(document.activeElement?.id).toBe("inside");
  });

  it("does nothing for null", () => {
    overlay(`<button id="inside">x</button>`);
    document.getElementById("inside")!.focus();
    restoreFocusTo(null);
    expect(document.activeElement?.id).toBe("inside");
  });

  it("leaves focus alone when the element has been detached", () => {
    // Calling focus() on a detached node drops the caller to <body>, which is
    // worse than not restoring at all.
    overlay(`<button id="inside">x</button>`);
    const behind = render(document.getElementById("behind")!);
    behind.remove();
    document.getElementById("inside")!.focus();

    restoreFocusTo(behind);
    expect(document.activeElement?.id).toBe("inside");
  });

});


describe("the <video controls> case", () => {
  // Two of these four discriminate — they FAIL if video[controls] is dropped
  // from FOCUSABLE again: "puts the player in the Tab cycle" and "wraps
  // backward ... ONTO the player". The other two pass under either scheme and
  // are kept as documentation of the document-order boundary, not as guards.
  // Saying which is which, because the pair they replaced claimed to cover this
  // case and did not.
  it("puts the player in the Tab cycle", () => {
    const el = overlay(`<button id="copy">copy</button><video id="v" controls></video>`);
    const tags = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].map((n) => n.tagName);
    expect(tags).toEqual(["BUTTON", "VIDEO"]);
  });

  it("wraps backward from the first control ONTO the player", () => {
    // The discriminating case. With video absent from FOCUSABLE, `last` is the
    // Close button and shift-Tab from Copy lands there — leaving the player
    // unreachable in both directions, which is what a single-clip modal did.
    //
    // Asserted by spying on focus() rather than reading activeElement: jsdom
    // does not treat <video> as focusable, so calling focus() on one is a no-op
    // there. What the trap *chose* is the behaviour under test; whether jsdom
    // honours it is jsdom's business.
    const el = overlay(`
      <button id="copy">copy</button>
      <button id="close">close</button>
      <video id="v" controls></video>
    `);
    const video = document.getElementById("v")!;
    let focused = false;
    video.focus = () => { focused = true; };
    document.getElementById("copy")!.focus();

    const e = tab(true);
    expect(trapTabWithin(el, e)).toBe(true);
    expect(focused).toBe(true);
  });

  it("wraps forward from the player to the first control", () => {
    const el = overlay(`<button id="copy">copy</button><video id="v" controls></video>`);
    const video = document.getElementById("v")!;
    expect(nextFocusableAfter(el, video)).toBeNull();

    setActiveElement(video);
    const e = tab();
    expect(trapTabWithin(el, e)).toBe(true);
    expect(e.defaultPrevented).toBe(true);
  });

  it("lets Tab through when a control follows the player", () => {
    // The multi-clip shape: header, player, prev/next. Document order decides,
    // so the trap stays out of the way until there is genuinely nothing after.
    const el = overlay(`
      <button id="copy">copy</button>
      <video id="v" controls></video>
      <button id="next">next</button>
    `);
    const video = document.getElementById("v")!;
    expect(nextFocusableAfter(el, video)?.id).toBe("next");

    setActiveElement(video);
    const e = tab();
    expect(trapTabWithin(el, e)).toBe(false);
    expect(e.defaultPrevented).toBe(false);
  });

  it("leaves document.activeElement alone for the tests that follow", () => {
    // Guards the afterEach above, and is placed last on purpose: without the
    // cleanup this reads the frozen, detached <video> from the two tests above
    // and fails. A stubbed global that outlives its test is invisible until
    // someone appends a suite here and cannot work out why it fails.
    const el = overlay(`<button id="copy">copy</button>`);
    const copy = el.querySelector<HTMLElement>("#copy")!;
    copy.focus();
    expect(document.activeElement).toBe(copy);
  });
});


describe("previousFocusableBefore", () => {
  // nextFocusableAfter is covered through trapTabWithin's forward-wrap tests;
  // the backward one is only reached on shift-Tab, so it gets its own.
  it("finds the nearest preceding control, not the first", () => {
    const el = overlay(`
      <button id="a">a</button>
      <button id="b">b</button>
      <button id="c">c</button>
    `);
    const c = el.querySelector<HTMLElement>("#c")!;
    expect(previousFocusableBefore(el, c)?.id).toBe("b");
  });

  it("returns null at the start of the container", () => {
    const el = overlay(`<button id="a">a</button><button id="b">b</button>`);
    const a = el.querySelector<HTMLElement>("#a")!;
    expect(previousFocusableBefore(el, a)).toBeNull();
  });

  it("returns null for an unlisted element with nothing focusable before it", () => {
    // The ClipModal shape: a tabindex="-1" card holding focus on open, with the
    // controls inside it. shift-Tab from there must wrap rather than fall out
    // of the overlay, and this null is what tells trapTabWithin to do that.
    const el = overlay(`<div id="card" tabindex="-1"><button id="copy">copy</button></div>`);
    const card = el.querySelector<HTMLElement>("#card")!;
    expect(previousFocusableBefore(el, card)).toBeNull();
  });
});
