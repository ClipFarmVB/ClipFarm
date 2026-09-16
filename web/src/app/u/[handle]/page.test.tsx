/**
 * CF-304 round 3: the profile page must not carry one handle's failure to the
 * next one.
 *
 * `ProfileView` keeps `profile`, `error` and `loading` in state and fetches in
 * an effect keyed on `handle`. The effect re-runs on a handle change; the state
 * does not reset, and `react-hooks/set-state-in-effect` refuses the version
 * that resets it there. So the fix is a `key` on the element, and the defect is
 * the absence of one — which means the assertion has to be about what THIS file
 * renders, not about `ProfileView`.
 *
 * Rendering `<ProfileView key={handle} …>` in a component test would pass
 * whether or not `page.tsx` passes a key, since the test would be supplying it.
 * That is the shape this PR has already shipped once: a test that passes for a
 * reason other than the one it names.
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/features", () => ({ SOCIAL_ENABLED: true }));
vi.mock("./ProfileView", () => ({ ProfileView: () => null }));

import ProfilePage from "./page";

describe("the profile route", () => {
  it("keys the view on the handle, so a navigation remounts it", async () => {
    const el = await ProfilePage({ params: Promise.resolve({ handle: "bob" }) });

    expect(el.key).toBe("bob");
  });

  it("gives two handles two different keys", async () => {
    // States the property the remount actually needs — two handles, two keys —
    // rather than one sample value, and it IS load-bearing.
    //
    // An earlier version of this comment said the opposite, on the strength of
    // two mutations: no key, and `key="profile"`. Both turned the test above
    // red, so the conclusion was that this one adds nothing. The mutation set
    // was the problem: `"profile"` is not `"bob"`, so the first test failed on
    // the sample value rather than on the constancy. `key="bob"` is the input
    // that separates them, and under it only THIS test goes red.
    const a = await ProfilePage({ params: Promise.resolve({ handle: "alice" }) });
    const b = await ProfilePage({ params: Promise.resolve({ handle: "bob" }) });

    expect(a.key).not.toBe(b.key);
  });
});
