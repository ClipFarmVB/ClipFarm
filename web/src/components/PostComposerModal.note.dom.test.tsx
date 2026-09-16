// @vitest-environment jsdom
//
// CF-109b item 3 (#398): the composer's half of the signposting.
//
// The settings copy is pinned in `ProfileSettingsForm.dom.test.tsx`, and the
// argument there applies here with more force: this note exists to stop a user
// going to the privacy switch expecting it to unlock the greyed-out tiers, and
// a revert of it to the *same false claim* the card is about would otherwise be
// invisible. Reverting the note entirely, and inverting it to "the
// Private/Public switch in settings controls who can see a clip", both left the
// suite green before this file.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ createPost: vi.fn() }));

import { PostComposerModal } from "@/components/PostComposerModal";
import type { Clip, Visibility } from "@/lib/api";

function makeClip(ceiling: Visibility): Clip {
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
    effective_visibility: ceiling,
  } as unknown as Clip;
}

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

function mount(ceiling: Visibility) {
  act(() => {
    root.render(<PostComposerModal clip={makeClip(ceiling)} onClose={() => {}} />);
  });
}

function cardText(): string {
  return (
    document.querySelector('[role="dialog"]')?.textContent ?? ""
  ).replace(/\s+/g, " ");
}

describe("what the composer says about the greyed-out tiers", () => {
  it("says raising a clip is not built, rather than implying another clip might allow it", () => {
    // Each disabled tier's own reason reads as a property of THIS clip. None
    // does: no endpoint writes a clip's or a game's visibility, so `private` is
    // the ceiling on everything, for everyone.
    mount("private");
    expect(cardText()).toContain("Raising a clip's visibility isn't built yet");
    expect(cardText()).toContain("every post is Only me for now");
  });

  it("sends the user away from the privacy switch, not towards it", () => {
    // The whole point of item 3. The obvious move after being told "private" is
    // to go flip Private/Public in settings, which governs follow approval and
    // nothing else — so they would find no change and no explanation.
    mount("private");
    expect(cardText()).toContain(
      "The Private/Public switch in settings controls who can follow you, not who can see a clip",
    );
  });

  it("never states the inverse, which is the claim this card exists to delete", () => {
    // The exact shape of the regression: the same false sentence, relocated.
    mount("private");
    expect(cardText()).not.toContain("settings controls who can see a clip.");
    expect(cardText()).not.toContain("controls what people can see");
  });

  it("is not the least readable text in the dialog", () => {
    // CF-109b moved the settings correction off `text-subtle` because a
    // sentence replacing a false safety claim should not be the hardest one to
    // read. The same argument applies here with more force: on
    // `bg-surface-high` in the default dark theme `text-subtle` measures
    // 1.84:1, and this is the only surface saying every post is Only me and
    // that the settings switch is not the visibility control.
    //
    // `text-muted` is 3.52:1 -- still under the 4.5:1 AA floor, which needs a
    // token change rather than a class swap, but roughly double.
    mount("private");
    const note = [...document.querySelectorAll('[role="dialog"] p')].find((el) =>
      (el.textContent ?? "").includes("isn't built yet"),
    );
    expect(note).toBeDefined();
    expect(note!.className).toContain("text-muted");
    expect(note!.className).not.toContain("text-subtle");
  });

  it("does not nag when the clip already supports every tier", () => {
    // A note about an unbuilt setter is noise on a clip that needs no setter.
    mount("public");
    expect(cardText()).not.toContain("isn't built yet");
  });
});
