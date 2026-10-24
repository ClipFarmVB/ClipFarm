/**
 * Which tier the composer offers, and what it asks for first.
 *
 * CF-109 greyed out every tier above the clip's own, because nothing in the
 * product could raise a clip's visibility and those options could only ever end
 * in a 409 whose suggested remedy did not exist. CF-109b (#398) built the write
 * path, so the same arithmetic now decides what needs the user's CONSENT rather
 * than what is refused — hence `tierNeedsRaise` rather than `tierBlocked`.
 *
 * Asserted as a table against the same matrix `test_posts.py` uses for
 * `access.at_most`, because the two orderings of the same three values have to
 * agree — and the UI copy is the one that fails on submit if it drifts wide.
 */
import { describe, expect, it, vi } from "vitest";

// `tierUnavailable` is imported inside its own describe instead: it closes over
// a build-time flag read at module load, so the tests re-import the module with
// the flag stubbed rather than asserting whatever the test env happened to set.
import { tierNeedsRaise } from "@/components/PostComposerModal";
import type { Visibility } from "@/lib/api";

const TIERS: Visibility[] = ["private", "followers", "public"];

describe("tierNeedsRaise", () => {
  it("needs no consent for a tier at or below the clip's own", () => {
    expect(tierNeedsRaise("private", "private")).toBe(false);
    expect(tierNeedsRaise("private", "followers")).toBe(false);
    expect(tierNeedsRaise("followers", "followers")).toBe(false);
    expect(tierNeedsRaise("followers", "public")).toBe(false);
    expect(tierNeedsRaise("public", "public")).toBe(false);
  });

  it("needs consent for anything wider than the clip", () => {
    expect(tierNeedsRaise("followers", "private")).toBe(true);
    expect(tierNeedsRaise("public", "private")).toBe(true);
    expect(tierNeedsRaise("public", "followers")).toBe(true);
  });

  it("treats a missing ceiling as private, not as unrestricted", () => {
    // The fail-closed direction, and it still is one after CF-109b: a clip
    // payload from a path that hasn't been taught to resolve the ceiling asks
    // for consent it may not need, rather than silently widening footage whose
    // current tier it could not read.
    expect(tierNeedsRaise("private", undefined)).toBe(false);
    expect(tierNeedsRaise("followers", undefined)).toBe(true);
    expect(tierNeedsRaise("public", undefined)).toBe(true);
  });

  it("leaves at least one option needing no consent, for every ceiling", () => {
    // A composer where every option asks to widen the footage would train the
    // user to tick without reading. `private` never does, because a post can
    // always be narrower than its clip.
    for (const ceiling of TIERS) {
      expect(TIERS.some((t) => !tierNeedsRaise(t, ceiling))).toBe(true);
      expect(tierNeedsRaise("private", ceiling)).toBe(false);
    }
  });
});

describe("tierUnavailable", () => {
  // The deployment flag, which is a different question from the ceiling: no
  // consent makes `public` available, so it stays disabled with a reason.
  // Read at module load, so these drive it by re-importing.
  async function reload(enabled: boolean) {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_PUBLIC_POSTING_ENABLED", enabled ? "true" : "false");
    return (await import("@/components/PostComposerModal")).tierUnavailable;
  }

  it("hides only Everyone, and only while the deployment has it off", async () => {
    const off = await reload(false);
    expect(off("public")).toBe(true);
    expect(off("followers")).toBe(false);
    expect(off("private")).toBe(false);
  });

  it("offers Everyone once the deployment turns it on", async () => {
    const on = await reload(true);
    expect(on("public")).toBe(false);
  });

  it("never hides a tier the ceiling is about", async () => {
    // The two rules must stay separate. Folding them together would grey out
    // `followers` on a private clip again, which is the dead end CF-109b
    // exists to remove.
    const off = await reload(false);
    for (const tier of TIERS) {
      if (tier !== "public") expect(off(tier)).toBe(false);
    }
  });
});
