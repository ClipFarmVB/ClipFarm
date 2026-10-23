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

// The real class: `@/lib/api` is mocked above, which is why ProfileView takes
// `ApiError` from `@/lib/apiError` rather than from the client.
import { ApiError } from "@/lib/apiError";
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

describe("a failed load (CF-304, CF-423)", () => {
  async function renderFailure(rejection: unknown) {
    getProfile.mockRejectedValue(rejection);
    await act(async () => {
      root.render(<ProfileView handle="alice" />);
    });
  }

  function heading() {
    return host.querySelector("h1")?.textContent;
  }

  const notFound = () => new ApiError("Profile not found", 404);
  const serverFault = () => new ApiError("Internal Server Error", 500);

  it.each([
    ["a 404", notFound],
    ["a 500", serverFault],
  ])("does not tell the visitor the handle is free on %s", async (_label, make) => {
    // This branch rendered "No one is using @alice" for ANY failure, so a 500
    // or a dropped connection asserted the availability of a handle that may
    // well be taken. The 404 arm is held to it too: the API also answers 404
    // for an account whose handle was generated, so absence here is not
    // availability.
    await renderFailure(make());

    expect(host.textContent).not.toContain("No one is using");
    expect(host.textContent).not.toContain("available");
  });

  it("says there is no profile for a handle that does not exist", async () => {
    // The common failure. `getProfile` is `Promise<Profile>` and a missing
    // handle is a 404, so it rejects — and the status, not the server's
    // prose, is what routes it here.
    await renderFailure(notFound());

    expect(heading()).toBe("No profile at @alice");
    expect(host.textContent).not.toContain("Couldn't load");
  });

  it("reports a server fault as a failure to load, with the server's words", async () => {
    await renderFailure(serverFault());

    expect(heading()).toBe("Couldn't load @alice");
    expect(host.textContent).toContain("Internal Server Error");
  });

  it("reports a rate limit as a failure to load, not as absence", async () => {
    // The profile route is rate limited per address; a 429 says nothing about
    // the handle.
    await renderFailure(new ApiError("Rate limit exceeded", 429));

    expect(heading()).toBe("Couldn't load @alice");
  });

  it("reports a dropped connection as a failure to load, not as absence", async () => {
    // fetch rejects with a TypeError when the network is gone: no status at
    // all, so nothing to say about whether the profile exists.
    await renderFailure(new TypeError("Failed to fetch"));

    expect(heading()).toBe("Couldn't load @alice");
    expect(host.textContent).toContain("Failed to fetch");
  });

  it("does not read a 404 out of the message text", async () => {
    // A plain Error whose message happens to be the 404's `detail` is still
    // not a 404: the branch is on the status, never on server prose.
    await renderFailure(new Error("Profile not found"));

    expect(heading()).toBe("Couldn't load @alice");
  });

  it("does not throw inside its own catch when the rejection is not an Error", async () => {
    // Asserting the rendered text would NOT catch this: `setError(undefined)`
    // still renders "Please try again.", because the paragraph is
    // `{error ?? …}`. The first version of this test did exactly that and
    // passed against the unguarded code.
    //
    // The difference a `null` rejection makes is that `e.message` throws
    // *inside* the catch, so the chain rejects with nobody left to handle it.
    const unhandled: unknown[] = [];
    const onUnhandled = (e: unknown) => unhandled.push(e);
    process.on("unhandledRejection", onUnhandled);
    try {
      getProfile.mockRejectedValue(null);
      await act(async () => {
        root.render(<ProfileView handle="alice" />);
      });
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }

    expect(unhandled).toEqual([]);
    expect(host.textContent).toContain("Please try again.");
  });

  it("distinguishes a 404 from a 500 by its heading, not only its detail line", async () => {
    // States the contract directly: absence and a fault must not read the
    // same. Compared on the heading, because the detail line would differ
    // even if both landed in one arm — that is all CF-304 could do, and it
    // is what CF-423 (#556) exists to go beyond.
    await renderFailure(notFound());
    const notFoundHeading = heading();
    act(() => root.unmount());
    host.remove();
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await renderFailure(serverFault());

    expect(heading()).toBeTruthy();
    expect(heading()).not.toBe(notFoundHeading);
  });
});

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

  it("does not tell a stranger to follow, because following does not exist", async () => {
    // There is no follows table and no follows router; `is_follower` returns
    // False unconditionally until CF-110. This sentence was the only "Follow"
    // call to action anywhere in web/src, so it pointed at nothing -- and it
    // restated the model this card exists to correct, on the page above the
    // grid. The settings screen in this same change says "isn't built yet";
    // this is that standard applied to the second surface.
    await render(true);
    expect(host.textContent).not.toMatch(/Follow to see/i);
    expect(host.textContent).toContain("following isn't built yet");
    expect(host.textContent).toContain("not who can see its clips");
  });

  it("says nothing about privacy for a public account", async () => {
    await render(false);
    expect(host.textContent).not.toContain("This account is private");
    expect(grid()).not.toBeNull();
  });
});
