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
// says nothing about it. These cases make that omission fail here instead, for call sites in
// Sidebar — every one that exists today, and not the CF-248 case of a third
// one in another file. A third link inside Sidebar fails `has both of them`,
// so it cannot be added without a deliberate update to this file — it fails
// four cases, since the length guards below pin the count too.
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
// PostComposerModal.dom.test.tsx and PostGrid.dom.test.tsx both predate
// this and are left alone here — run together they emit 29 of these.
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
    // The mobile top bar and the desktop rail. Both are unconditional JSX —
    // neither is gated on `open`, `user` or `isDesktop` — so a drop to one is a
    // real change rather than a mock coincidence. A *third* link inside Sidebar
    // fails this case and both cases below, which is deliberate: adding one
    // should require saying so here.
    expect(brandLinks().length).toBe(2);
  });

  it("renders the shared mark, not a copy of it that can drift", () => {
    // What CF-260 is for. The suite finds these links by the wordmark's text,
    // so inlining the mark's markup back into a call site keeps every other
    // case green — and the duplication this PR removed is back, silently.
    //
    // Comparing each call site against a standalone render catches that the
    // moment the two disagree, which is the harm: an inlined copy is identical
    // on the day it is written and drifts on the day someone edits one of them.
    // It does not catch an inlined copy that stays byte-identical forever, and
    // nothing short of asserting on the import could.
    act(() => root.render(<BrandMark />));
    const shared = host.innerHTML;
    act(() => root.unmount());
    root = createRoot(host);

    const links = brandLinks();

    // Same length guard as the coupling cases below, and for the same reason: a
    // bare loop over an empty list passes, and this helper empties on a
    // wordmark change.
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link.innerHTML).toBe(shared);
    }
  });

  it("supplies `group`, so the hover is not dead", () => {
    // The CF-353 failure shape: omitting `group` breaks nothing visible to the
    // compiler, the linter or the renderer — the hover simply never fires.
    const links = brandLinks();

    // Not decoration. `brandLinks` finds links by the wordmark's own text, so
    // changing that text empties the list — and a bare `for` over an empty list
    // passes. Measured before this line existed: with the wordmark mutated AND
    // `group` and `gap-2.5` stripped from both call sites, these two cases went
    // green while both couplings were fully broken. Neither failure message
    // would have said `group`, so a maintainer fixing the wordmark string would
    // have arrived at an all-green suite over a dead hover — the exact CF-353
    // shape, one level up. `has both of them` cannot cover this: it is a
    // separate `it`, and a vacuous pass here is still a pass.
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link.className.split(/\s+/)).toContain("group");
    }
  });

  it("supplies the flex row and the gap that lay the two children out", () => {
    // `flex` as well as `gap-2.5`: `gap` is inert on a box that is not flex or
    // grid, so asserting the gap alone pins one third of the coupling the
    // component's own docblock documents. Dropping `flex` from a call site left
    // all seven cases passing before this. "It breaks visibly" is the argument
    // that was already wrong about `group`.
    const links = brandLinks();

    expect(links).toHaveLength(2);
    for (const link of links) {
      const classes = link.className.split(/\s+/);

      expect(classes).toContain("flex");
      expect(classes).toContain("gap-2.5");
    }
  });
});
