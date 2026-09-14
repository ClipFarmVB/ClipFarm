// @vitest-environment jsdom
//
// CF-109b (#398): consent given for one tier is not consent for a wider one.
//
// Its own file because the property needs "Everyone" to be offerable, and
// `PUBLIC_POSTING_ENABLED` is read once, when the composer's module loads. The
// sibling consent suite runs with the shipped default (off), where "Everyone"
// is disabled, so its tier-change test can only go Followers → Only me →
// Followers. That is a narrowing change, and a narrowing change resets the
// tick both in the real `choose` and in one that resets it only when the tier
// gets narrower. This goes Followers → Everyone, which only the real one
// passes.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Hoisted above the imports below, so the flag is set before the composer's
// module — and the features module it reads — is evaluated.
const createPost = vi.hoisted(() => {
  process.env.NEXT_PUBLIC_PUBLIC_POSTING_ENABLED = "true";
  return vi.fn();
});
vi.mock("@/lib/api", () => ({ createPost }));

import { PostComposerModal } from "@/components/PostComposerModal";
import type { Clip } from "@/lib/api";

function makeClip(): Clip {
  return {
    id: "clip-1",
    game_id: "game-1",
    action_type: "spike",
    confidence: 0.9,
    start_time: 10,
    end_time: 18,
    clip_url: "https://example.test/c.mp4",
    thumbnail_url: null,
    labels: [],
    effective_visibility: "private",
  } as unknown as Clip;
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  createPost.mockResolvedValue({});
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
});

afterAll(() => {
  delete process.env.NEXT_PUBLIC_PUBLIC_POSTING_ENABLED;
});

function card(): HTMLElement {
  return document.querySelector('[role="dialog"]') as HTMLElement;
}
function tier(label: string): HTMLButtonElement {
  return [...card().querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes(label),
  ) as HTMLButtonElement;
}
function postButton(): HTMLButtonElement {
  return [...card().querySelectorAll("button")].find(
    (b) => (b.textContent ?? "").trim() === "Post",
  ) as HTMLButtonElement;
}
function consentBox(): HTMLInputElement | null {
  return card().querySelector('input[type="checkbox"]');
}
async function click(el: Element) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}
async function tick() {
  // Through the prototype setter, as in the sibling suite: React treats a
  // direct `.checked` assignment as a no-op change.
  const box = consentBox()!;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "checked",
    )!.set!;
    setter.call(box, true);
    box.dispatchEvent(new Event("click", { bubbles: true }));
  });
}

describe("widening after consenting", () => {
  it("does not carry a tick given for Followers over to Everyone", async () => {
    act(() => {
      root.render(<PostComposerModal clip={makeClip()} onClose={() => {}} />);
    });

    // The premise. With the flag unset "Everyone" is disabled, clicking it
    // changes nothing, and every assertion below would pass for that reason.
    expect(tier("Everyone").disabled).toBe(false);

    await click(tier("Followers"));
    await tick();
    expect(postButton().disabled).toBe(false);

    await click(tier("Everyone"));

    expect(consentBox()!.checked).toBe(false);
    expect(postButton().disabled).toBe(true);
    await click(postButton());
    expect(createPost).not.toHaveBeenCalled();
  });
});
