// @vitest-environment jsdom
//
// CF-304 items 1 and 2: a failure here used to be indistinguishable from an
// empty account.
//
// `getCollections()` had no `.catch`, so a network failure left `collections`
// at `[]` and the render's `collections.length === 0` branch told the user
// "No collections yet — create one below." That is an affirmative claim about
// their account produced by a dropped connection, and it invites them to
// create a duplicate of something they already have.
//
// Separate file from `CollectionPickerModal.dom.test.tsx` because that one
// mocks `getCollections` as a static resolved promise shared by all three of
// its cases; these need per-case control, and converting the mock there would
// put the portal tests at risk for no reason.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getCollections = vi.hoisted(() => vi.fn());
const createCollection = vi.hoisted(() => vi.fn());
const addClipToCollection = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ getCollections, createCollection, addClipToCollection }));

import { CollectionPickerModal } from "@/components/CollectionPickerModal";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal("scrollTo", vi.fn());
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

async function mount() {
  await act(async () => {
    root.render(<CollectionPickerModal clipId="clip-1" onClose={() => {}} />);
  });
  const card = document.querySelector<HTMLElement>('[aria-label="Save clip to a collection"]');
  if (!card) throw new Error("picker did not render");
  return card;
}

describe("a failed collections fetch (CF-304)", () => {
  it("does not claim the user has no collections", async () => {
    getCollections.mockRejectedValue(new Error("Failed to fetch"));
    const card = await mount();

    // The absence is the assertion. A fix that showed an error AND kept the
    // empty-state copy would still be telling the user something false.
    expect(card.textContent).not.toContain("No collections yet");
    expect(card.textContent).toContain("Failed to fetch");
  });

  it("still shows the empty state when the account really is empty", async () => {
    // The other direction, so the fix cannot be "delete the empty state".
    getCollections.mockResolvedValue([]);
    const card = await mount();

    expect(card.textContent).toContain("No collections yet");
  });

  it("does not leave the fetch rejecting unhandled", async () => {
    const unhandled: unknown[] = [];
    const onUnhandled = (e: unknown) => unhandled.push(e);
    process.on("unhandledRejection", onUnhandled);
    try {
      getCollections.mockRejectedValue(new Error("Failed to fetch"));
      await mount();
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }

    expect(unhandled).toEqual([]);
  });
});
