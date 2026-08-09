import { afterEach, describe, expect, it, vi } from "vitest";

import { clearMe, fetchMe, setMe } from "./useMe";
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
});
