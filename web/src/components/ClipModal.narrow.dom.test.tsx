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
// A signed-in user with a claimed handle, so the composer opener renders —
// `canPost` gates on both.
vi.mock("@/lib/useMe", () => ({
  useMe: () => ({ id: "u1", username: "alice", username_is_generated: false }),
  needsHandle: () => false,
}));
vi.mock("@/lib/download", () => ({ startCrossOriginDownload: vi.fn() }));
// A stub composer: what is under test is the hand-back, not the composer, and
// the real one pulls in `createPost` and a focus trap this file does not model.
vi.mock("@/components/PostComposerModal", () => ({
  PostComposerModal: ({
    onPosted,
  }: {
    onPosted?: (raised: string | null) => void;
  }) => (
    <button id="stub-posted" onClick={() => onPosted?.("public")}>
      pretend we posted and widened
    </button>
  ),
}));

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

function mount(
  effective: Visibility | null,
  onUpdate: (c: Clip) => void = () => {},
  ownsClip = true,
) {
  act(() => {
    root.render(
      <ClipModal
        clip={makeClip(effective)}
        onClose={() => {}}
        onUpdate={onUpdate}
        ownsClip={ownsClip}
      />,
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

  it("offers exactly one control here, and it narrows", async () => {
    // Stepping between the wider tiers is a choice rather than a retraction,
    // and the composer owns that direction with the consent it needs. A picker
    // here would put an unconfirmed widening one mis-click from the undo.
    //
    // Asserted over the RENDERED CONTROLS, not over the calls one known-good
    // click produces. The first version did the latter and did not back its own
    // claim: adding a literal "Make public" button beside the undo left it
    // green, because nothing clicked it.
    setClipVisibility.mockResolvedValue(makeClip("private"));
    mount("public");

    const section = narrowButton()!.closest("div")!;
    const controls = [...section.querySelectorAll("button")];
    expect(controls).toHaveLength(1);
    expect(controls[0]).toBe(narrowButton());

    await click(narrowButton()!);
    for (const call of setClipVisibility.mock.calls) {
      expect(call[1]).toBe("private");
    }
  });

  it("offers nothing to someone who does not own the clip", async () => {
    // Collections span owners, so a viewer can be looking at someone else's
    // public clip. The API is owner-gated and 404s, so the control could only
    // ever answer "Clip not found" over a clip they are watching.
    mount("public", () => {}, false);
    expect(narrowButton()).toBeUndefined();
  });

  it("does not fire twice while the first request is in flight", async () => {
    let release: (c: Clip) => void = () => {};
    setClipVisibility.mockReturnValue(new Promise<Clip>((r) => (release = r)));
    mount("public");

    const button = narrowButton()!;
    await click(button);
    expect(button.disabled).toBe(true);
    await click(button);
    await act(async () => release(makeClip("private")));

    expect(setClipVisibility).toHaveBeenCalledTimes(1);
  });

  it("does not carry an error from one clip over to the next", async () => {
    // `composingFor` is keyed to the clip a few lines above for this exact
    // reason. Unkeyed, a failure on this clip stayed mounted under the next one
    // the arrow keys reached, claiming a failure that was not its.
    setClipVisibility.mockRejectedValue(new Error("Clip not found"));
    mount("public");
    await click(narrowButton()!);
    expect(document.querySelector('[role="alert"]')).not.toBeNull();

    await act(async () => {
      root.render(
        <ClipModal
          clip={{ ...makeClip("public"), id: "clip-2" } as Clip}
          onClose={() => {}}
          ownsClip
        />,
      );
    });

    expect(document.querySelector('[role="alert"]')).toBeNull();
  });

  it("hands the widened clip back so the undo it promises can appear", async () => {
    // The consent copy promises "you can make it private again from the clip".
    // The composer widens it server-side and THIS dialog holds the copy that
    // decides whether the control renders — so without the hand-back the
    // promise is unreachable until a page reload, in exactly the flow that
    // makes it.
    const onUpdate = vi.fn();
    mount("private", onUpdate);
    expect(narrowButton()).toBeUndefined(); // private: nothing to take back yet

    await click(
      document.querySelector('[title="Post this clip"]') as HTMLButtonElement,
    );
    await click(document.getElementById("stub-posted")!);

    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ id: "clip-1", effective_visibility: "public" }),
    );
  });


  it("says what it does to posts of the clip, and what it cannot do", async () => {
    mount("public");
    const text = (document.querySelector('[role="dialog"]')?.textContent ?? "").replace(
      /\s+/g,
      " ",
    );
    expect(text).toContain("Posts of this clip stay up");
    // The half worth keeping honest. `update_clip_visibility`'s own docstring
    // says a presigned URL minted before the change keeps working until it
    // expires, and both /share and the player mint 3600s links — so "stops
    // anyone else seeing it", with no caveat, was the one sentence in this
    // control that overstated what it does.
    expect(text).toContain("A link you already shared may keep working for up to an hour");
  });

  it("renders nothing at all when the social surface is off", async () => {
    // The flag is read at module load, so this re-imports with it stubbed —
    // the same shape the composer's own suite uses for PUBLIC_POSTING_ENABLED.
    vi.resetModules();
    vi.doMock("@/lib/features", () => ({ SOCIAL_ENABLED: false }));
    const { ClipModal: Gated } = await import("@/components/ClipModal");

    await act(async () => {
      root.render(
        <Gated clip={makeClip("public")} onClose={() => {}} ownsClip />,
      );
    });

    expect(narrowButton()).toBeUndefined();
    vi.doUnmock("@/lib/features");
    vi.resetModules();
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
