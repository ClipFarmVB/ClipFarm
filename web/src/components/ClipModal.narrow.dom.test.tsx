// @vitest-environment jsdom
//
// CF-109b (#398): taking a published clip back.
//
// The composer can widen a clip, with a confirmation. Until this, nothing could
// narrow one — so publishing was a one-way door, and on youth-sports footage
// that is the wrong shape: deleting the post does not narrow the clip, so the
// only undo was deleting the footage.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const setClipVisibility = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  setClipVisibility,
  getClipDownloadUrl: vi.fn(),
  getClipShareUrl: vi.fn(),
}));
vi.mock("@/lib/features", () => ({ SOCIAL_ENABLED: true }));
vi.mock("@/lib/useMe", () => ({ useMe: () => null, needsHandle: () => false }));
vi.mock("@/lib/download", () => ({ startCrossOriginDownload: vi.fn() }));

import { ClipModal } from "@/components/ClipModal";
import type { Clip, Visibility } from "@/lib/api";

function makeClip(effective: Visibility | null): Clip {
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
    effective_visibility: effective,
  } as unknown as Clip;
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  HTMLMediaElement.prototype.load = vi.fn();
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
});

function mount(effective: Visibility | null, onUpdate = () => {}) {
  act(() => {
    root.render(
      <ClipModal clip={makeClip(effective)} onClose={() => {}} onUpdate={onUpdate} />,
    );
  });
}

function narrowButton(): HTMLButtonElement | undefined {
  return [...document.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("private again"),
  ) as HTMLButtonElement | undefined;
}

async function click(el: Element) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("taking a clip back", () => {
  it("offers the undo on a clip that is visible to someone else", () => {
    mount("followers");
    expect(narrowButton()).toBeDefined();
  });

  it("offers nothing on a clip that is already private", () => {
    // Which today is every clip, so this control renders only for someone who
    // has actually published.
    mount("private");
    expect(narrowButton()).toBeUndefined();
  });

  it("offers nothing when the tier is unknown", () => {
    // Fail closed: a payload that never carried the field is not evidence the
    // clip is published, and offering a retraction for something that may
    // already be private is a control that can only confuse.
    mount(null);
    expect(narrowButton()).toBeUndefined();
  });

  it("narrows to private and hands the parent the row the API returned", async () => {
    // The parent owns the grid behind this dialog; without the callback its
    // badge keeps showing the old tier until a refetch.
    const updated = makeClip("private");
    setClipVisibility.mockResolvedValue(updated);
    const onUpdate = vi.fn();
    mount("public", onUpdate);

    await click(narrowButton()!);

    expect(setClipVisibility).toHaveBeenCalledWith("clip-1", "private");
    expect(onUpdate).toHaveBeenCalledWith(updated);
  });

  it("only ever narrows — there is no widening control here", async () => {
    // Stepping between the wider tiers is a choice rather than a retraction,
    // and the composer owns that direction with the consent it needs. A picker
    // here would put an unconfirmed widening one mis-click from the undo.
    setClipVisibility.mockResolvedValue(makeClip("private"));
    mount("followers");

    await click(narrowButton()!);

    for (const call of setClipVisibility.mock.calls) {
      expect(call[1]).toBe("private");
    }
  });

  it("says what it does to posts of the clip, not only to the clip", async () => {
    mount("public");
    const text = document.querySelector('[role="dialog"]')?.textContent ?? "";
    expect(text).toContain("Posts of this clip stay up");
  });

  it("surfaces a refusal instead of failing silently", async () => {
    setClipVisibility.mockRejectedValue(new Error("Clip not found"));
    const onUpdate = vi.fn();
    mount("public", onUpdate);

    await click(narrowButton()!);

    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("Clip not found");
    expect(onUpdate).not.toHaveBeenCalled();
  });
});
