// @vitest-environment jsdom
//
// The first test in this project that needs a DOM. Opted in per-file rather
// than flipping vitest.config.mts to jsdom globally: every other suite here is
// pure logic and has no reason to pay for a document.
//
// No React renderer is involved. useFocusTrap is a thin useEffect around the
// two functions below, and those are where the behaviour that has already been
// got wrong once lives — so they are what gets pinned.
import { beforeEach, describe, expect, it } from "vitest";

import {
  FOCUSABLE,
  nextFocusableAfter,
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

beforeEach(() => {
  document.body.innerHTML = "";
});


describe("FOCUSABLE", () => {
  it("excludes a media element, deliberately", () => {
    // <video controls> IS focusable in a browser, but listing it makes it a
    // wrap boundary — and Tab from the player is how a keyboard user reaches
    // the seek bar and volume inside its shadow controls. nextFocusableAfter
    // handles it instead; see the "focusable but unlisted" cases below.
    const el = overlay(`<button id="a">a</button><video controls></video>`);
    const tags = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].map((n) => n.tagName);
    expect(tags).toEqual(["BUTTON"]);
  });

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


describe("focusable but unlisted (the <video controls> case)", () => {
  it("does not wrap when something focusable follows the video", () => {
    // The common ClipModal shape: header buttons, the player, then prev/next.
    // Tab from the video must fall through so the browser can walk into the
    // player's own shadow controls.
    const el = overlay(`
      <button id="copy">copy</button>
      <video id="v" controls></video>
      <button id="next">next</button>
    `);
    const video = document.getElementById("v")!;
    video.focus();
    // jsdom will not focus a <video>; assert on the decision, not on focus.
    expect(nextFocusableAfter(el, video)?.id).toBe("next");

    const e = tab();
    Object.defineProperty(el.ownerDocument, "activeElement", {
      value: video, configurable: true,
    });
    expect(trapTabWithin(el, e)).toBe(false);
    expect(e.defaultPrevented).toBe(false);
  });

  it("wraps when the video is the last thing in the overlay", () => {
    // A single-clip modal has no prev/next. There is nothing after the player,
    // so a forward Tab would leave the overlay entirely — the leak that made
    // listing <video> tempting in the first place.
    const el = overlay(`<button id="copy">copy</button><video id="v" controls></video>`);
    const video = document.getElementById("v")!;
    expect(nextFocusableAfter(el, video)).toBeNull();

    Object.defineProperty(el.ownerDocument, "activeElement", {
      value: video, configurable: true,
    });
    const e = tab();
    expect(trapTabWithin(el, e)).toBe(true);
    expect(e.defaultPrevented).toBe(true);
  });
});
