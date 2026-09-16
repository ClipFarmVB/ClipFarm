// @vitest-environment jsdom
//
// The composer must never tell a user that the Private/Public switch in
// settings controls who can see a clip. It does not: `users.is_private`
// governs whether following needs approval and nothing else, which
// `services/access.py` argues at length and
// `test_account_privacy_does_not_clamp_post_visibility` pins.
//
// This file arrived with CF-109b item 3 (#480) as the guard on the note that
// said so -- the one that told a user raising a clip was not built yet. #398
// built the setter and this PR spends it: the composer now offers the raise
// behind an explicit confirmation instead of explaining that it cannot. The
// note is gone, and with it the three tests that pinned its copy and its
// contrast class -- #480's own comment on those lines named this PR as what
// would delete them. `PostComposerModal.consent.dom.test.tsx` covers what
// replaced it.
//
// What outlives the note is the claim it existed to keep out of the dialog.
// Both regressions #480 checked red -- restoring the false sentence, and
// relocating it -- still fail here.
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

describe("what the composer says about a clip it cannot post as-is", () => {
  it("never states the inverse, which is the claim this card exists to delete", () => {
    // The exact shape of the regression: the same false sentence, relocated.
    // `private` is the ceiling that puts the widening path on screen, so it is
    // the render where a well-meant explanation is most likely to reappear.
    mount("private");
    expect(cardText()).not.toContain("settings controls who can see a clip.");
    expect(cardText()).not.toContain("controls what people can see");
  });
});
