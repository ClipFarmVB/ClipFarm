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
const likePost = vi.hoisted(() => vi.fn());
const unlikePost = vi.hoisted(() => vi.fn());
const getComments = vi.hoisted(() => vi.fn());
const createComment = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  getClipDownloadUrl,
  getClipShareUrl,
  likePost,
  unlikePost,
  getComments,
  createComment,
  deleteComment: vi.fn(),
}));
vi.mock("@/lib/download", () => ({ startCrossOriginDownload }));
vi.mock("@/lib/useMe", () => ({ useMe: () => null }));

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

// The play effect used to swallow `play()`'s rejection and did not depend on
// `muted`. `muted` is page state shared by every card, so once the user
// unmuted, the next post to become active called `play()` unmuted from a
// scroll-driven effect — which iOS refuses — and the poster sat there frozen.
// Re-muting, the obvious recovery, changed nothing the effect depended on, so
// it never retried; there was no play button; the only escape was to swipe
// away and back. Every post after the first unmute, on the platform the card
// names as the target.
function mountActive(post: Post, muted: boolean) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <FeedPost post={post} active loaded muted={muted} onToggleSound={() => {}} />,
    );
  });
}

describe("when autoplay is refused", () => {
  it("shows a play affordance instead of a frozen poster", async () => {
    HTMLMediaElement.prototype.play = vi
      .fn()
      .mockRejectedValue(new DOMException("denied", "NotAllowedError"));
    await act(async () => mountActive(makePost(), false));

    expect(byLabel("Play")).toHaveLength(1);
  });

  it("does not show it while playback is fine", async () => {
    await act(async () => mountActive(makePost(), true));
    expect(byLabel("Play")).toHaveLength(0);
  });

  it("retries play() when the mute state changes", async () => {
    const play = vi.fn().mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = play;
    await act(async () => mountActive(makePost(), false));
    const calls = play.mock.calls.length;
    expect(calls).toBeGreaterThan(0);

    await act(async () => {
      root.render(
        <FeedPost post={makePost()} active loaded muted onToggleSound={() => {}} />,
      );
    });
    // Re-muting is the user's recovery gesture; it must re-run the effect.
    expect(play.mock.calls.length).toBeGreaterThan(calls);
  });

  it("recovers on tap, which is a gesture the browser allows", async () => {
    const play = vi
      .fn()
      .mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"))
      .mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = play;
    await act(async () => mountActive(makePost(), false));
    expect(byLabel("Play")).toHaveLength(1);

    await click(byLabel("Play")[0]);
    expect(byLabel("Play")).toHaveLength(0);
  });
});

// The like button is optimistic, which is the whole reason it needs pinning:
// the fill has to land on the tap (a like that waits for a round trip reads as
// a dropped tap), the server's count has to win afterwards (someone else's
// like arrived while this one was in flight), and a rejection has to put the
// old state back rather than leave a red heart on a like that never happened.
function railCount(label: string): string | null {
  const button = byLabel(label)[0];
  return button ? (button.textContent ?? "") : null;
}

describe("liking a post", () => {
  it("fills in before the server answers, then takes the server's count", async () => {
    let release: (v: { liked: boolean; like_count: number }) => void = () => {};
    likePost.mockReturnValue(new Promise((r) => (release = r)));
    mount(makePost()); // like_count 3, viewer_has_liked false

    await click(byLabel("Like")[0]);

    // Optimistic: the label flipped and the count moved without a response.
    expect(byLabel("Unlike")).toHaveLength(1);
    expect(railCount("Unlike")).toContain("4");

    // 9, not 4: a like from someone else landed while this one was in flight.
    // Trusting the optimistic arithmetic here would show a stale count until
    // the next refetch, which is why both writes return the full state.
    await act(async () => release({ liked: true, like_count: 9 }));
    expect(railCount("Unlike")).toContain("9");
    expect(likePost).toHaveBeenCalledWith("post-1");
  });

  it("puts the previous state back when the write fails", async () => {
    likePost.mockRejectedValue(new Error("network"));
    mount(makePost());

    await click(byLabel("Like")[0]);

    expect(byLabel("Like")).toHaveLength(1);
    expect(railCount("Like")).toContain("3");
    expect(railCount("Like")).toContain("Failed");
  });

  it("unlikes when the viewer has already liked it", async () => {
    unlikePost.mockResolvedValue({ liked: false, like_count: 2 });
    mount({ ...makePost(), viewer_has_liked: true } as Post);

    expect(byLabel("Unlike")).toHaveLength(1);
    await click(byLabel("Unlike")[0]);

    expect(unlikePost).toHaveBeenCalledWith("post-1");
    expect(likePost).not.toHaveBeenCalled();
    expect(railCount("Like")).toContain("2");
  });

  it("sends one request for a double tap", async () => {
    let release: (v: { liked: boolean; like_count: number }) => void = () => {};
    likePost.mockReturnValue(new Promise((r) => (release = r)));
    mount(makePost());

    await click(byLabel("Like")[0]);
    // Second tap lands on the flipped label while the first is still in
    // flight. Without the busy ref this sent an unlike that raced the like.
    await click(byLabel("Unlike")[0]);
    await act(async () => release({ liked: true, like_count: 4 }));

    expect(likePost).toHaveBeenCalledTimes(1);
    expect(unlikePost).not.toHaveBeenCalled();
  });
});

describe("the comment sheet", () => {
  it("opens from the rail and reports the count the card was given", async () => {
    getComments.mockResolvedValue({ items: [], next_cursor: null });
    mount(makePost()); // comment_count 1

    expect(railCount("Comments")).toContain("1");
    expect(document.querySelector('[role="dialog"]')).toBeNull();

    await click(byLabel("Comments")[0]);

    // The sheet portals to document.body, not into the card's container.
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(getComments).toHaveBeenCalledWith("post-1", null);
  });

  it("moves the rail count when a comment is posted inside the sheet", async () => {
    // The sheet reports a delta rather than the card refetching the post, so
    // the rail count is only right if the card applies it. Nothing else would
    // catch a card that renders `post.comment_count` forever.
    getComments.mockResolvedValue({ items: [], next_cursor: null });
    createComment.mockResolvedValue({
      id: "c1",
      post_id: "post-1",
      body: "first",
      created_at: "2026-08-01T12:00:00+00:00",
      author: { id: "user-2", username: "bob", display_name: "Bob", avatar_url: null },
    });
    mount(makePost()); // comment_count 1
    await click(byLabel("Comments")[0]);

    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    const textarea = dialog.querySelector("textarea") as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )!.set!;
      setter.call(textarea, "first");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const post = [...dialog.querySelectorAll("button")].find((b) =>
      (b.textContent ?? "").startsWith("Post"),
    )!;
    await click(post);

    expect(railCount("Comments")).toContain("2");
  });
});
