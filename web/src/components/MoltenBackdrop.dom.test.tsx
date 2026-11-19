// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { MoltenBackdrop } from "./MoltenBackdrop";

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
});
