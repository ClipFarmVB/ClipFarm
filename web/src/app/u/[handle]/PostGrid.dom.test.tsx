// @vitest-environment jsdom
//
// CF-109: the grid is scoped to the viewer, so it has to re-ask when the
// viewer changes.
//
// The regression: the load effect depended on `[handle]` alone. The route does
// not change when you sign out, so nothing re-ran, and every private post
// stayed on screen — tile, tier icon and thumbnail — in a session that could no
// longer have requested any of them. The private half of the grid is precisely
// what must not survive a sign-out.
//
// `isSelf` cannot stand in for this. It answers "is this my profile", which is
// false for a stranger whether they are signed in or out, so a grid keyed on it
// misses every case except your own page.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getUserPosts = vi.fn();
vi.mock("@/lib/api", () => ({
  getUserPosts: (...a: unknown[]) => getUserPosts(...a),
  deletePost: vi.fn(),
}));

// Mutable so a test can change who is asking between renders, which is the
// whole scenario. `useMe` itself is covered by its own suite.
let viewer: { id: string } | null = null;
vi.mock("@/lib/useMe", () => ({
  useMe: () => viewer,
  needsHandle: () => false,
}));

import { PostGrid } from "./PostGrid";

const post = (id: string, visibility: string) => ({
  id,
  clip_id: "c1",
  caption: null,
  visibility,
  like_count: 0,
  comment_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  author: { id: "u1", username: "alice", display_name: "Alice", avatar_url: null },
  playback: {
    clip_url: "https://x.test/c.mp4",
    thumbnail_url: "https://x.test/t.jpg",
    proxy_url: null,
    start_time: 0,
    end_time: 5,
  },
});

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  viewer = { id: "u1" };
  getUserPosts.mockReset();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function render(isSelf: boolean) {
  await act(async () => {
    root.render(<PostGrid handle="alice" isSelf={isSelf} />);
  });
}

describe("PostGrid viewer scoping", () => {
  it("re-requests when the viewer changes, and drops what the new one may not see", async () => {
    getUserPosts.mockResolvedValueOnce([post("p-public", "public"), post("p-private", "private")]);
    await render(true);

    expect(getUserPosts).toHaveBeenCalledTimes(1);
    expect(host.textContent).not.toContain("No posts yet");
    // Two tiles while signed in as the author.
    expect(host.querySelectorAll("img").length).toBe(2);

    // Sign out. The route is unchanged; only the viewer is.
    viewer = null;
    getUserPosts.mockResolvedValueOnce([post("p-public", "public")]);
    await render(false);

    expect(getUserPosts).toHaveBeenCalledTimes(2);
    expect(host.querySelectorAll("img").length).toBe(1);
  });

  it("does not re-request when nothing about the viewer changed", async () => {
    getUserPosts.mockResolvedValue([post("p-public", "public")]);
    await render(true);
    await render(true);

    // Keyed on the viewer's id, not the Me object: useMe republishes a fresh
    // object to every subscriber on a rename or avatar upload, and refetching
    // the grid because someone changed their display name is a request for
    // nothing.
    viewer = { id: "u1" };
    await render(true);

    expect(getUserPosts).toHaveBeenCalledTimes(1);
  });
});

// ── CF-109b item 2 (#398): a post can be watched from here ───────────────────
//
// The grid rendered thumbnails and no player, on the argument that playback is
// CF-112's feed. True, and it left a published clip watchable nowhere — the
// profile is the only surface that shows posts, and your own posts are not in
// your own feed, so this hit the author first.

function tiles(): HTMLElement[] {
  return [...host.querySelectorAll("[data-comment-id], .group")] as HTMLElement[];
}
function playButtons(): HTMLButtonElement[] {
  return [...host.querySelectorAll("button")].filter((b) =>
    (b.getAttribute("aria-label") ?? "").startsWith("Play"),
  ) as HTMLButtonElement[];
}
function dialog(): HTMLElement | null {
  return document.querySelector('[role="dialog"]');
}
async function click(el: Element) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("playing a post from the grid", () => {
  it("gives every tile one keyboard-reachable play control", async () => {
    getUserPosts.mockResolvedValue([post("p1", "private"), post("p2", "public")]);
    await render(true);

    // A button, not an onClick on the tile: a grid of watchable things should
    // have one tab stop per thing.
    expect(playButtons()).toHaveLength(2);
    expect(tiles().length).toBeGreaterThan(0);
  });

  it("opens a player on the post that was clicked, not the first one", async () => {
    // Two posts with different URLs, and the SECOND clicked. With one post the
    // test's own name is untestable: `setPlaying(posts[0])` passes.
    const second = post("p2", "public");
    second.playback.clip_url = "https://x.test/second.mp4";
    getUserPosts.mockResolvedValue([post("p1", "public"), second]);
    await render(false);
    expect(dialog()).toBeNull();

    await click(playButtons()[1]);

    const video = dialog()?.querySelector("video");
    expect(video).not.toBeNull();
    expect(video?.getAttribute("src")).toBe("https://x.test/second.mp4");
    expect(video?.hasAttribute("autoplay")).toBe(true);
  });

  it("names the post in the play control rather than labelling them all alike", async () => {
    // Fifty tiles announcing "Play this post" are fifty identical controls.
    // A prefix match on "Play" cannot see this: both versions produce it.
    const captioned = post("p1", "public");
    captioned.caption = "match point" as never;
    getUserPosts.mockResolvedValue([captioned]);
    await render(false);

    expect(playButtons()[0].getAttribute("aria-label")).toContain("match point");
  });

  it("says the visibility tier in words, not only as an icon", async () => {
    // The badge is `pointer-events-none` so the play target underneath stays
    // clickable, which also means the browser never renders its `title` — and
    // a `title` on a bare span was never a reliable accessible name anyway.
    getUserPosts.mockResolvedValue([post("p1", "followers")]);
    await render(false);

    expect(host.textContent).toContain("Visible to your followers");
  });

  it("traps the keyboard in the player and closes on Escape", async () => {
    // The player sits over a grid full of focusable tiles. Deleting the focus
    // trap entirely left the suite green.
    getUserPosts.mockResolvedValue([post("p1", "public")]);
    await render(false);
    await click(playButtons()[0]);

    expect(dialog()?.contains(document.activeElement)).toBe(true);

    await act(async () => {
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }),
      );
    });
    expect(dialog()).toBeNull();
  });

  it("closes again", async () => {
    getUserPosts.mockResolvedValue([post("p1", "public")]);
    await render(false);
    await click(playButtons()[0]);

    const close = [...(dialog()?.querySelectorAll("button") ?? [])].find(
      (b) => b.getAttribute("aria-label") === "Close",
    )!;
    await click(close);

    expect(dialog()).toBeNull();
  });

  it("stacks the delete control above the full-frame play target", async () => {
    // The play affordance covers the whole frame, so the owner's Remove button
    // has to sit above it or it is unclickable — "delete is broken", and only
    // on the author's own profile, which is the one place it matters.
    //
    // Asserted on the z-index classes rather than by clicking, and the
    // distinction is the point: jsdom does no layout, so `dispatchEvent` on the
    // Remove button reaches its handler whatever is painted over it. A click
    // test here would pass against a version where the control is genuinely
    // buried. This compares the two orders, which is the part jsdom can see.
    // A captioned post, so the caption assertion below actually runs — the
    // shared fixture has `caption: null`, and a guarded assertion on an
    // element that never renders is not an assertion.
    const captioned = post("p1", "private");
    captioned.caption = "nice dig" as never;
    getUserPosts.mockResolvedValue([captioned]);
    await render(true);

    const z = (el: Element | null | undefined) => {
      const hit = (el?.className ?? "").toString().match(/(?:^|\s)z-(\d+)/);
      return hit ? Number(hit[1]) : 0;
    };
    const play = playButtons()[0];
    const remove = [...host.querySelectorAll("button")].find(
      (b) => b.getAttribute("aria-label") === "Remove this post",
    );
    expect(remove).toBeDefined();
    expect(z(remove)).toBeGreaterThan(z(play));

    // The caption is painted over the frame too and must not eat the tap.
    const caption = [...host.querySelectorAll("p")].find((el) =>
      (el.textContent ?? "").includes("nice dig"),
    );
    expect(caption).toBeDefined();
    expect(caption!.className).toContain("pointer-events-none");

    // Same for the tier badge. Smaller consequence — a dead corner rather than
    // a dead strip — but the same class of bug, and free to pin here.
    // The badge is the span wrapping the sr-only tier text — it no longer
    // carries a `title`, because `pointer-events-none` means a browser would
    // never render one.
    const badge = [...host.querySelectorAll("span")].find((el) =>
      el.querySelector("span.sr-only"),
    );
    expect(badge).toBeDefined();
    expect(badge!.className).toContain("pointer-events-none");
  });

  it("says so rather than showing an empty frame when there is no clip URL", async () => {
    const broken = post("p1", "public");
    broken.playback.clip_url = "";
    getUserPosts.mockResolvedValue([broken]);
    await render(false);

    await click(playButtons()[0]);

    expect(dialog()?.querySelector("video")).toBeNull();
    expect(dialog()?.textContent).toContain("isn't available to play");
  });
});
