import { afterEach, describe, expect, it, vi } from "vitest";

import { clearMe, fetchMe, needsHandle, setMe } from "./useMe";
import * as api from "./api";

const profile = (username: string | null) =>
  ({
    id: "u1",
    username,
    display_name: null,
    bio: null,
    avatar_url: null,
    is_private: true,
    created_at: "2026-01-01T00:00:00Z",
    email: "a@b.com",
    username_changed_at: null,
    username_is_generated: false,
  }) as api.Me;

afterEach(() => {
  clearMe();
  vi.restoreAllMocks();
});

describe("useMe cache", () => {
  it("fetches once and serves the cached profile afterwards", async () => {
    const spy = vi.spyOn(api, "getMe").mockResolvedValue(profile("setter_07"));

    expect((await fetchMe())?.username).toBe("setter_07");
    expect((await fetchMe())?.username).toBe("setter_07");

    // The sidebar, banner and profile page all call this — one request, not N.
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("de-duplicates concurrent callers into a single request", async () => {
    const spy = vi.spyOn(api, "getMe").mockResolvedValue(profile("setter_07"));

    await Promise.all([fetchMe(), fetchMe(), fetchMe()]);

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("resolves to null when signed out rather than rejecting", async () => {
    // /users/me 401s for a signed-out caller. Callers render the signed-out
    // state; an unhandled rejection here would surface as a console error on
    // every page load.
    vi.spyOn(api, "getMe").mockRejectedValue(new Error("API error 401"));

    await expect(fetchMe()).resolves.toBeNull();
  });

  it("does not cache a failed lookup", async () => {
    const spy = vi
      .spyOn(api, "getMe")
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(profile("setter_07"));

    expect(await fetchMe()).toBeNull();
    // A transient failure must not leave the user permanently signed-out-looking.
    expect((await fetchMe())?.username).toBe("setter_07");
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("serves a pushed profile without refetching", async () => {
    const spy = vi.spyOn(api, "getMe").mockResolvedValue(profile("old_name"));

    // What the settings page does after a rename, so the sidebar updates
    // immediately instead of showing the stale handle.
    setMe(profile("new_name"));

    expect((await fetchMe())?.username).toBe("new_name");
    expect(spy).not.toHaveBeenCalled();
  });

  it("clearMe forces the next caller to refetch", async () => {
    const spy = vi.spyOn(api, "getMe").mockResolvedValue(profile("setter_07"));

    await fetchMe();
    clearMe(); // sign-out
    await fetchMe();

    // Otherwise the next user sees the previous one's handle in the chrome.
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("an in-flight fetch does not repopulate the cache after clearMe", async () => {
    // The race the other clearMe test can't see, because it awaits first:
    // clearMe() drops the reference to the request but cannot cancel it, so
    // without a generation check the signed-out user's profile lands in the
    // cache *after* sign-out and the chrome keeps showing their handle.
    let landResponse!: (me: api.Me) => void;
    const spy = vi
      .spyOn(api, "getMe")
      .mockReturnValueOnce(
        new Promise<api.Me>((resolve) => {
          landResponse = resolve;
        })
      )
      .mockResolvedValueOnce(profile("second_user"));

    const inflight = fetchMe(); // first user's request, still open
    clearMe(); // sign out mid-flight
    landResponse(profile("first_user")); // their response arrives late
    await inflight;

    expect((await fetchMe())?.username).toBe("second_user");
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("setMe is not overwritten by a fetch that was already in flight", async () => {
    // Claiming a handle on a slow connection: the sidebar's shared fetchMe() is
    // still open when the settings form saves and pushes the fresh profile. The
    // older response then resolves with the pre-claim body and, without a
    // generation bump in setMe, reverts the chrome and brings the banner back.
    let landResponse!: (me: api.Me) => void;
    vi.spyOn(api, "getMe").mockReturnValueOnce(
      new Promise<api.Me>((resolve) => {
        landResponse = resolve;
      })
    );

    const inflight = fetchMe(); // started before the claim
    setMe(profile("claimed_name")); // the user saves
    landResponse(profile(null)); // pre-claim response lands late
    await inflight;

    expect((await fetchMe())?.username).toBe("claimed_name");
  });
});

describe("needsHandle", () => {
  it("is true when no handle has been claimed", () => {
    expect(needsHandle(profile(null))).toBe(true);
  });

  it("is true for a handle the backfill generated", () => {
    expect(
      needsHandle({ ...profile("alice"), username_is_generated: true })
    ).toBe(true);
  });

  it("is false once a handle has been chosen", () => {
    expect(needsHandle(profile("alice"))).toBe(false);
  });

  it("is false while the profile is unknown", () => {
    // Signed out or still loading — staying quiet beats prompting on a guess.
    expect(needsHandle(null)).toBe(false);
  });

  it("gates the profile link the same way the API gates the route", () => {
    // The sidebar and the settings hint both link to /u/{handle}, which 404s
    // for a generated handle. `Boolean(username) && !needsHandle(me)` is the
    // condition both use — a truthiness check on username alone sends a
    // backfilled user to "No one is using @alice".
    const linkable = (me: api.Me | null) =>
      Boolean(me?.username) && !needsHandle(me);

    expect(linkable(profile("alice"))).toBe(true);
    expect(linkable({ ...profile("alice"), username_is_generated: true })).toBe(false);
    expect(linkable(profile(null))).toBe(false);
    expect(linkable(null)).toBe(false);
  });
});
