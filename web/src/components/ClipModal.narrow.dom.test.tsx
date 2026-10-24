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
    <>
      <button id="stub-posted" onClick={() => onPosted?.("public")}>
        pretend we posted and widened
      </button>
      <button id="stub-posted-untouched" onClick={() => onPosted?.(null)}>
        pretend we posted without touching the clip
      </button>
    </>
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
    // Which is every clip nobody has posted wider, so this control renders
    // only for someone who has actually published.
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

  it("narrows to private and hands the parent its own row with the new tier", async () => {
    // The parent owns the grid behind this dialog; without the callback its
    // badge keeps showing the old tier until a refetch.
    //
    // Its own row, not the PATCH echo. The echo carries no `player_name`, and
    // with R2 a freshly presigned `clip_url`, so swapping it in dropped the
    // tagged player from the header and restarted the video. The echo here is
    // shaped like that on purpose, so a version that forwards it goes red.
    const shown = { ...makeClip("public"), player_name: "Sam" } as Clip;
    const echo = {
      ...makeClip("private"),
      player_name: null,
      clip_url: "https://example.test/re-signed.mp4",
    } as unknown as Clip;
    setClipVisibility.mockResolvedValue(echo);
    const onUpdate = vi.fn();
    act(() => {
      root.render(<ClipModal clip={shown} onClose={() => {}} onUpdate={onUpdate} ownsClip />);
    });

    await click(narrowButton()!);

    expect(setClipVisibility).toHaveBeenCalledWith("clip-1", "private");
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onUpdate.mock.calls[0][0]).toEqual({ ...shown, effective_visibility: "private" });
  });

  it("offers exactly one control here, and it narrows", async () => {
    // Stepping between the wider tiers is a choice rather than a retraction,
    // and the composer owns that direction with the consent it needs. A picker
    // beside the undo would put an unconfirmed widening one mis-click from it.
    //
    // Asserted over the RENDERED CONTROLS in the undo's own section, not over
    // the calls one known-good click produces. The first version did the
    // latter and did not back its own claim: adding a literal "Make public"
    // button beside the undo left it green, because nothing clicked it.
    //
    // What this does NOT pin: a widening control elsewhere in this dialog,
    // outside the undo's section. A review round showed one added a level up
    // stays green here. Guarding the whole dialog would mean exercising every
    // other control it holds (share, download, the composer), which this test
    // does not attempt; its scope is the undo's section.
    setClipVisibility.mockResolvedValue(makeClip("private"));
    mount("public");

    const section = narrowButton()!.closest("div")!;
    const controls = [...section.querySelectorAll("button")];
    expect(controls).toHaveLength(1);
    expect(controls[0]).toBe(narrowButton());

    await click(narrowButton()!);
    // Without this the loop below passes when nothing was called at all.
    expect(setClipVisibility).toHaveBeenCalledTimes(1);
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

  it("offers nothing when the caller does not say the viewer owns the clip", () => {
    // The default is what keeps this off the collections page, which is
    // cross-owner and passes no `ownsClip` at all. Every other test here passes
    // the prop explicitly, so flipping the default to true left them all green.
    act(() => {
      root.render(<ClipModal clip={makeClip("public")} onClose={() => {}} />);
    });
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

  it("does not show another clip's request as in flight", async () => {
    // The in-flight flag used to be a bare boolean, so while a request was out
    // for this clip the NEXT clip's button read "Making private…" and sat
    // disabled until it settled — a hung request locked the undo on every clip
    // the arrow keys reached.
    const undo = () =>
      [...document.querySelectorAll("button")].find((b) =>
        /Make this clip private again|Making private/.test(b.textContent ?? ""),
      ) as HTMLButtonElement | undefined;
    let release: (c: Clip) => void = () => {};
    setClipVisibility.mockReturnValue(new Promise<Clip>((r) => (release = r)));
    mount("public");

    await click(undo()!);
    expect(undo()!.disabled).toBe(true); // the premise: this clip's request is out

    await act(async () => {
      root.render(
        <ClipModal clip={{ ...makeClip("public"), id: "clip-2" } as Clip} onClose={() => {}} ownsClip />,
      );
    });
    expect(undo()!.disabled).toBe(false);
    expect(undo()!.textContent).toContain("Make this clip private again");

    await act(async () => release(makeClip("private")));
  });

  it("puts the undo in a section of its own, not in the footer's row", () => {
    // The footer is a single non-wrapping flex row; as an item in it the button
    // and its two sentences were squeezed into whatever width was left. jsdom
    // does no layout, so the structure is what can be pinned here.
    mount("public");
    const post = document.querySelector('[title="Post this clip"]') as HTMLElement;
    expect(post).not.toBeNull();
    expect(post.parentElement!.contains(narrowButton()!)).toBe(false);
  });

  it("leaves the row alone when posting did not touch the clip", async () => {
    // A composer that reports null — a post at or under the clip's own tier —
    // must hand the parent nothing. Forwarding `effective_visibility: null`
    // would hide the undo on a clip that is still public.
    const onUpdate = vi.fn();
    mount("public", onUpdate);

    await click(document.querySelector('[title="Post this clip"]') as HTMLButtonElement);
    await click(document.getElementById("stub-posted-untouched")!);

    expect(onUpdate).not.toHaveBeenCalled();
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
    // The consent copy promises "you can make it private again from the clip on
    // its game's page". The composer widens it server-side and THIS dialog holds the copy that
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
    // Undone in `finally`, so a failing assertion cannot leave the flag mocked
    // off for every test after this one.
    try {
      const { ClipModal: Gated } = await import("@/components/ClipModal");

      await act(async () => {
        root.render(
          <Gated clip={makeClip("public")} onClose={() => {}} ownsClip />,
        );
      });

      expect(narrowButton()).toBeUndefined();
    } finally {
      vi.doUnmock("@/lib/features");
      vi.resetModules();
    }
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
