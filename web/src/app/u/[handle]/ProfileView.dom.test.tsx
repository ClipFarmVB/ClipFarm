// @vitest-environment jsdom
//
// CF-109b item 3 (#398): the profile must not tell a stranger the opposite of
// what the API does.
//
// `users.is_private` governs whether following needs approval and NOTHING else
// — `services/access.py` argues that at length and
// `test_account_privacy_does_not_clamp_post_visibility` pins it on the API
// side. So a private account's `public` post is deliberately readable by
// anyone, and `GET /posts?username=` serves it.
//
// This page used to render "This account is private. Follow to see their
// clips." INSTEAD of the grid, which meant the web reassured a stranger that
// the content was gone while the API went on serving it. That is the worse of
// the two ways to be wrong, and it contradicted both the settings copy and this
// file's own docstring — the comment saying "hiding the grid entirely would
// contradict the API" sat inside the branch hiding the grid entirely.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getProfile = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ getProfile }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "stranger" }, loading: false }),
}));
vi.mock("@/lib/useMe", () => ({ useMe: () => ({ id: "stranger" }) }));
// A marker, not the real grid: what is under test is whether the page renders
// it at all, not what it renders.
vi.mock("./PostGrid", () => ({
  PostGrid: ({ handle }: { handle: string }) => <div data-grid={handle} />,
}));

import { ProfileView } from "./ProfileView";

const profile = (isPrivate: boolean) => ({
  id: "u1",
  username: "alice",
  display_name: "Alice",
  bio: null,
  avatar_url: null,
  is_private: isPrivate,
  username_is_generated: false,
});

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
  vi.clearAllMocks();
});

async function render(isPrivate: boolean) {
  getProfile.mockResolvedValue(profile(isPrivate));
  await act(async () => {
    root.render(<ProfileView handle="alice" />);
  });
}

function grid() {
  return host.querySelector("[data-grid]");
}

describe("a private account seen by a stranger", () => {
  it("still renders the grid, because the account flag does not gate content", async () => {
    // Nothing leaks: `getUserPosts` filters by visibility in SQL for the asking
    // viewer, so a stranger sees the public posts and no others — today, none.
    await render(true);
    expect(grid()).not.toBeNull();
  });

  it("says the account is private as a notice rather than in place of the grid", async () => {
    await render(true);
    expect(host.textContent).toContain("This account is private");
    expect(grid()).not.toBeNull();
  });

  it("says nothing about privacy for a public account", async () => {
    await render(false);
    expect(host.textContent).not.toContain("This account is private");
    expect(grid()).not.toBeNull();
  });
});
