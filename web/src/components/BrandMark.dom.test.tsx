// @vitest-environment jsdom
//
// CF-260 / CF-353: the extraction is only safe if something notices when it
// changes, and nothing did.
//
// The mark was extracted from two identical copies in Sidebar so that swapping
// the placeholder for the real logo is one edit rather than two (CF-248). That
// makes the component the single point of failure for how the brand renders,
// and the suite was blind to it: mutating `size={13}` to `size={99}` *and*
// replacing the wordmark text with "MUTATED" left all 154 tests passing.
// Typecheck is live on the file, so that was a coverage gap rather than a
// broken harness.
//
// **The second suite here is the one that matters.** BrandMark returns a bare
// fragment, so `group` — which drives its own `group-hover:` classes — and the
// `gap-2.5` that lays its two children out both live on the parent <Link>, at
// each call site, where the component cannot enforce them. A call site that
// omits `group` gets a silently dead hover: no type error, no lint warning, no
// test failure, because `group-hover:` is inert without an ancestor `group` and
// says nothing about it. These cases make that omission fail here instead.
//
// They assert the coupling from both ends deliberately. Asserting only that
// BrandMark emits `group-hover:` would pass while every call site had dropped
// `group`; asserting only that the links carry `group` would pass after the
// component stopped needing it.
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/games" }));
// A plain anchor, so className lands in the DOM where the call-site cases can
// read it. next/link's own behaviour is not what is under test.
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: ComponentProps<"a">) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1" }, loading: false, signOut: vi.fn() }),
}));
vi.mock("@/contexts/ThemeContext", () => ({
  useTheme: () => ({ theme: "light", toggle: vi.fn() }),
}));
vi.mock("@/lib/useMe", () => ({
  useMe: () => null,
  needsHandle: () => true,
  clearMe: vi.fn(),
}));
vi.mock("@/lib/useIsDesktopLayout", () => ({ useIsDesktopLayout: () => false }));
vi.mock("@/lib/useBodyScrollLock", () => ({ useBodyScrollLockBelowLg: vi.fn() }));

import { BrandMark } from "@/components/BrandMark";
import { Sidebar } from "@/components/Sidebar";

// React 19 wants this set before `act` runs, and prints "The current testing
// environment is not configured to support act(...)" at every call without it.
// The warning is harmless and reads exactly like a fault in the component under
// test, which is the sort of noise that gets a real message skimmed past.
// PostComposerModal.dom.test.tsx predates this and is left alone here.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("BrandMark", () => {
  function render() {
    act(() => root.render(<BrandMark />));
    return host;
  }

  it("renders the mark and the wordmark, and nothing else", () => {
    // A fragment of exactly two children: the icon box and the wordmark. A
    // third element, or a wrapper appearing around them, changes what every
    // call site's flex row lays out.
    expect(host.children.length).toBe(0);
    render();

    expect(host.children.length).toBe(2);
    expect(host.children[0].tagName).toBe("DIV");
    expect(host.children[1].tagName).toBe("SPAN");
  });

  it("spells the wordmark ClipFarm", () => {
    // The mutation that survived the old suite. Exact, not a substring: this is
    // the product's name as the user reads it.
    expect(render().querySelector("span")?.textContent).toBe("ClipFarm");
  });

  it("draws the glyph at 13px", () => {
    // The other surviving mutation. lucide-react writes `size` straight through
    // to width/height, so the rendered svg is checkable without knowing which
    // icon it is — and this survives the CF-248 swap to the real logo, which is
    // the point of not asserting on `Clapperboard` itself.
    const svg = render().querySelector("svg");

    expect(svg?.getAttribute("width")).toBe("13");
    expect(svg?.getAttribute("height")).toBe("13");
  });

  it("styles its hover through an ancestor `group`", () => {
    // Half of the contract the call-site cases below check from the other end.
    // If this ever stops being true, those cases are asserting a coupling that
    // no longer exists and should go with it.
    const box = render().children[0];

    expect(box.className).toContain("group-hover:");
  });
});

describe("Sidebar's BrandMark call sites", () => {
  // Every link that wraps a BrandMark, found by the mark's own markup rather
  // than by a selector that would have to be updated alongside it.
  function brandLinks(): HTMLAnchorElement[] {
    act(() => root.render(<Sidebar />));
    const spans = Array.from(host.querySelectorAll("span")).filter(
      (s) => s.textContent === "ClipFarm",
    );
    return spans.map((s) => s.closest("a")).filter((a): a is HTMLAnchorElement => a !== null);
  }

  it("has both of them", () => {
    // The mobile top bar and the desktop rail. If this drops to one, the case
    // below is silently checking half of what it says it checks.
    expect(brandLinks().length).toBe(2);
  });

  it("supplies `group`, so the hover is not dead", () => {
    // The CF-353 failure shape: omitting `group` breaks nothing visible to the
    // compiler, the linter or the renderer — the hover simply never fires.
    for (const link of brandLinks()) {
      expect(link.className.split(/\s+/)).toContain("group");
    }
  });

  it("supplies the gap that lays the two children out", () => {
    for (const link of brandLinks()) {
      expect(link.className.split(/\s+/)).toContain("gap-2.5");
    }
  });
});
