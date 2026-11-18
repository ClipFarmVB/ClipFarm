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
    expect(layer.className).toContain("fixed");
    expect(layer.textContent).toBe("");
  });

  it("draws the three panel colours in each corner group", () => {
    const groups = host.querySelectorAll("svg");

    expect(groups).toHaveLength(2);
    for (const svg of groups) {
      for (const colour of ["molten-red", "molten-white", "molten-green"]) {
        expect(svg.querySelector(`.${colour}`)).not.toBeNull();
      }
    }
  });
});
