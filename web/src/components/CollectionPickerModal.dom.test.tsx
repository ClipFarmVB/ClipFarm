// @vitest-environment jsdom
//
// CF-347: the picker must escape whatever the page wraps it in.
//
// It rendered inline, and both pages that open it wrap their content in
// `.fade-up` — whose `both`-filled animation leaves `transform: translateY(0)`
// applied permanently. A transform that is not `none` makes an element a
// containing block for `position: fixed` descendants, so `inset-0` sized the
// overlay to the *document* instead of the viewport and centred the dialog
// thousands of pixels below the fold. Mounted, visible, opacity 1, unreachable
// — measured at ovTop 76 / ovH 11304 / cardTop 5647 in a 608px viewport, stable
// across 60 frames.
//
// **This asserts the parent, not the position.** jsdom has no layout engine:
// `getBoundingClientRect` returns zeros, transforms establish nothing, and a
// test that checked coordinates here would pass just as happily against the
// broken code. What is checkable without layout is the structural property the
// fix actually establishes — the overlay hangs off `document.body` and not off
// whatever rendered it — and that is the one thing that makes the positioning
// independent of any ancestor. Verified against the mutation: reverting to a
// plain `return (...)` fails the first case below.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getCollections: () => Promise.resolve([]),
  createCollection: vi.fn(),
  addClipToCollection: vi.fn(),
}));

import { CollectionPickerModal } from "@/components/CollectionPickerModal";

let host: HTMLDivElement;
let wrapper: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // jsdom has no scrolling, and `useBodyScrollLock` genuinely calls
  // `window.scrollTo` to restore the reader's position on release. Without
  // this the unmount path floods the run with jsdom "not implemented" traces
  // that look like a fault in the component under test.
  vi.stubGlobal("scrollTo", vi.fn());

  // Stands in for the page's `.fade-up` wrapper: the element the overlay must
  // NOT be parented to, whatever else is true of it.
  wrapper = document.createElement("div");
  wrapper.className = "fade-up";
  wrapper.style.transform = "translateY(0)";
  document.body.appendChild(wrapper);

  host = document.createElement("div");
  wrapper.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  wrapper.remove();
  vi.unstubAllGlobals();
});

async function mount() {
  await act(async () => {
    root.render(<CollectionPickerModal clipId="clip-1" onClose={() => {}} />);
  });
  const card = document.querySelector<HTMLElement>('[aria-label="Save clip to a collection"]');
  if (!card) throw new Error("picker did not render");
  return card;
}

describe("CollectionPickerModal", () => {
  it("mounts its overlay on document.body, not inside the transformed wrapper", async () => {
    const card = await mount();
    const overlay = card.parentElement!;

    expect(overlay.parentElement).toBe(document.body);
    expect(wrapper.contains(overlay)).toBe(false);
  });

  it("still renders its dialog and header", async () => {
    // The portal must not cost the content — a fix that mounted an empty
    // overlay somewhere correct would satisfy the case above on its own.
    const card = await mount();

    expect(card.getAttribute("role")).toBe("dialog");
    expect(card.textContent).toContain("Save to collection");
  });

  it("cleans the portal up on unmount", async () => {
    await mount();
    act(() => root.unmount());

    expect(
      document.querySelector('[aria-label="Save clip to a collection"]'),
    ).toBeNull();

    root = createRoot(host); // afterEach unmounts again; leave it a live root
  });
});
