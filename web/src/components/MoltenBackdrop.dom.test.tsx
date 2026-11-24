// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { facesToCover, INITIAL_FACES, MoltenBackdrop } from "./MoltenBackdrop";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(<MoltenBackdrop />));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

describe("MoltenBackdrop", () => {
  it("is decoration only: hidden from assistive tech, inert to the pointer, no text", () => {
    const layer = host.querySelector("[data-testid=molten-backdrop]")!;

    expect(layer.getAttribute("aria-hidden")).toBe("true");
    expect(layer.className).toContain("pointer-events-none");
    expect(layer.className).toContain("absolute");
    expect(layer.textContent).toBe("");
  });

  it("scrolls with the page: fills its positioned parent rather than the viewport", () => {
    const layer = host.querySelector("[data-testid=molten-backdrop]")!;

    expect(layer.className).not.toContain("fixed");
    expect(layer.className).toContain("inset-0");
  });

  it("stacks one face per screen and alternates them, red hook first", () => {
    const faces = host.querySelectorAll("svg");

    expect(faces.length).toBeGreaterThanOrEqual(2);
    const hooks = Array.from(faces, (svg) =>
      Array.from(svg.querySelectorAll("path"), (p) => p.getAttribute("class")),
    );
    for (const [i, classes] of hooks.entries()) {
      // Every face has all three colours, and a face is screen-tall and
      // stretched to the screen rather than letterboxed.
      for (const colour of ["molten-red", "molten-white", "molten-green"]) {
        expect(classes).toContain(colour);
      }
      expect(faces[i].getAttribute("preserveAspectRatio")).toBe("none");
      expect(faces[i].getAttribute("class")).toContain("h-screen");
      // The second path is the top seam triangle, the colour of the top hook:
      // red on even faces, green on odd ones.
      expect(classes[1]).toBe(i % 2 === 0 ? "molten-red" : "molten-green");
    }
  });

  it("draws enough faces to cover the page however short the screen", () => {
    // jsdom has no layout, so give the layer and each face real heights:
    // the landing on a phone held sideways, 2114px of page on a 375px screen.
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
      this: Element,
    ) {
      const height = this.tagName.toLowerCase() === "svg" ? 375 : 2114;
      return { height } as DOMRect;
    });
    act(() => window.dispatchEvent(new Event("resize")));

    // ceil(2114 / 375) = 6 faces, 2250px, so nothing below them is bare.
    expect(host.querySelectorAll("svg").length).toBe(6);
  });
});

describe("facesToCover", () => {
  it("covers the layer with whole faces, at least one", () => {
    expect(facesToCover(900, 900)).toBe(1);
    expect(facesToCover(2114, 375)).toBe(6);
    expect(facesToCover(2130, 600)).toBe(4);
    expect(facesToCover(100, 900)).toBe(1);
    // A remainder under half a face still needs a whole face, or the foot
    // of the page goes bare: rounding to nearest would give 1 here.
    expect(facesToCover(1000, 900)).toBe(2);
  });

  it("falls back to the initial count before there is anything to measure", () => {
    expect(facesToCover(0, 0)).toBe(INITIAL_FACES);
    expect(facesToCover(2114, 0)).toBe(INITIAL_FACES);
  });
});
