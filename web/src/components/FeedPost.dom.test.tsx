// @vitest-environment jsdom
//
// CF-112 review: the two card behaviours that are not pure logic.
//
// `feedWindow.test.ts` covers the window and prefetch rules — they were
// extracted precisely so they could be asserted without a DOM. What stayed in
// the component is the part where a wrong call reads fine and fails in front of
// a user: a download control that navigated the feed away instead of
// downloading, and a full-frame button that put one invisible tab stop per post
// ahead of the real controls.
//
// `createRoot` + `act` rather than a testing library, matching
// `PostComposerModal.dom.test.tsx` and for the reason its docblock gives: React
// 19 ships `act` and `react-dom/client`, so this needs no new dependency.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FeedPost } from "./FeedPost";
import type { Post } from "@/lib/api";

const getClipDownloadUrl = vi.hoisted(() => vi.fn());
const getClipShareUrl = vi.hoisted(() => vi.fn());
const startCrossOriginDownload = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ getClipDownloadUrl, getClipShareUrl }));
vi.mock("@/lib/download", () => ({ startCrossOriginDownload }));

// jsdom implements neither, and the card calls them on mount.
beforeEach(() => {
  HTMLMediaElement.prototype.load = vi.fn();
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
});

let container: HTMLDivElement;
let root: Root;

function mount(post: Post, onToggleSound = () => {}) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <FeedPost
        post={post}
        active={false}
        loaded={false}
        muted
        onToggleSound={onToggleSound}
      />,
    );
  });
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

function makePost(): Post {
  return {
    id: "post-1",
    clip_id: "clip-1",
    caption: "nice dig",
    visibility: "public",
    like_count: 3,
    comment_count: 1,
    created_at: "2026-08-01T12:00:00+00:00",
    author: {
      id: "user-1",
      username: "alice",
      display_name: "Alice",
      avatar_url: null,
    },
    playback: {
      clip_url: "https://pub.example.com/clips/c.mp4?sig=abc",
      thumbnail_url: null,
      proxy_url: null,
      start_time: 1,
      end_time: 4,
      action_type: "spike",
      highlight_score: 0.82,
    },
    viewer_has_liked: false,
  } as Post;
}

function byLabel(label: string): HTMLButtonElement[] {
  return [...container.querySelectorAll("button")].filter(
    (b) => b.getAttribute("aria-label") === label,
  ) as HTMLButtonElement[];
}

async function click(el: Element) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("downloading a clip", () => {
  it("mints a download URL and starts it off the top-level context", async () => {
    getClipDownloadUrl.mockResolvedValue({ url: "https://r2.example/signed?disp=1" });
    mount(makePost());

    await click(byLabel("Download clip")[0]);

    expect(getClipDownloadUrl).toHaveBeenCalledWith("clip-1");
    expect(startCrossOriginDownload).toHaveBeenCalledWith(
      "https://r2.example/signed?disp=1",
    );
  });

  it("never renders a bare anchor at the presigned clip URL", () => {
    // The regression this replaces. `<a href={clip_url} download>` reads right
    // and cannot work: `download` is ignored cross-origin, R2 is a different
    // origin, and a plain presigned GET carries no Content-Disposition — so the
    // browser navigates and renders the mp4 where the feed used to be.
    mount(makePost());

    const hrefs = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs).not.toContain("https://pub.example.com/clips/c.mp4?sig=abc");
    expect(container.querySelector("a[download]")).toBeNull();
  });

  it("does not fire twice on a double tap", async () => {
    let release: (v: { url: string }) => void = () => {};
    getClipDownloadUrl.mockReturnValue(new Promise((r) => (release = r)));
    mount(makePost());

    const button = byLabel("Download clip")[0];
    await click(button);
    await click(button);
    await act(async () => release({ url: "https://r2.example/signed" }));

    expect(getClipDownloadUrl).toHaveBeenCalledTimes(1);
  });
});

describe("the sound controls", () => {
  it("exposes exactly one focusable mute control per card", () => {
    mount(makePost());
    // The full-frame tap target is a pointer affordance, not a second control.
    // As a focusable button with the same accessible name it put one invisible,
    // full-screen tab stop in front of the rail on every post — fifty of them
    // down a fifty-post scroll, each announced on entering the card.
    expect(byLabel("Unmute")).toHaveLength(1);

    const overlay = container.querySelector('button[aria-hidden="true"]');
    expect(overlay).not.toBeNull();
    expect(overlay?.getAttribute("tabindex")).toBe("-1");
  });

  it("still toggles sound when the frame itself is tapped", async () => {
    const onToggleSound = vi.fn();
    mount(makePost(), onToggleSound);

    await click(container.querySelector('button[aria-hidden="true"]') as Element);
    expect(onToggleSound).toHaveBeenCalledTimes(1);
  });
});
