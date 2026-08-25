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

import { FOCUSABLE, restoreFocusTo, trapTabWithin } from "@/lib/useFocusTrap";

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
  it("includes a media element with controls", () => {
    // ClipModal's subject is a <video controls>. Browsers make it focusable, so
    // omitting it would put the last trap stop before the player and let a
    // forward Tab from it escape into the grid behind the backdrop.
    const el = overlay(`<button id="a">a</button><video controls></video>`);
    const tags = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].map((n) => n.tagName);
    expect(tags).toEqual(["BUTTON", "VIDEO"]);
  });

  it("ignores a media element without controls", () => {
    const el = overlay(`<button id="a">a</button><video></video>`);
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
    const ids = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].map((n) => n.tagName);
    expect(ids).toEqual(["A", "BUTTON"]);
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
  it("restores focus to a connected, visible element", () => {
    overlay(`<button id="inside">x</button>`);
    const behind = document.getElementById("behind")!;
    document.getElementById("inside")!.focus();

    restoreFocusTo(behind);
    expect(document.activeElement?.id).toBe("behind");
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
    const behind = document.getElementById("behind")!;
    behind.remove();
    document.getElementById("inside")!.focus();

    restoreFocusTo(behind);
    expect(document.activeElement?.id).toBe("inside");
  });

  // NOT TESTED HERE, deliberately: the hidden-element branch.
  //
  // restoreFocusTo also refuses an element that is connected but not rendered
  // — the Sidebar case where closing the drawer by crossing into the desktop
  // layout runs the same cleanup while the hamburger is display:none. That
  // branch is `checkVisibility()`, a layout API, and jsdom implements no layout
  // at all: no checkVisibility, offsetParent always null, getClientRects()
  // always empty. So there is nothing here that could distinguish a hidden
  // element from a visible one, and a test asserting it would be asserting the
  // capability check rather than the behaviour.
  //
  // Saying so rather than writing a test that passes for the wrong reason.
});
