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
