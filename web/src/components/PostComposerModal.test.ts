/**
 * CF-109: the composer may not offer a tier the clip cannot support.
 *
 * The review finding this covers: with no write path for a clip's or a game's
 * visibility anywhere in the product, and both defaulting to private, two of
 * the composer's three options could only ever end in a 409 whose suggested
 * remedy did not exist. The gate is what turns that dead end into a limit the
 * user can read.
 *
 * Asserted as a table against the same matrix `test_posts.py` uses for
 * `access.at_most`, because the two orderings have to agree — and the UI copy
 * is the one that fails on submit if it drifts wide.
 */
import { describe, expect, it } from "vitest";

import { tierBlocked } from "@/components/PostComposerModal";
import type { Visibility } from "@/lib/api";

const TIERS: Visibility[] = ["private", "followers", "public"];

describe("tierBlocked", () => {
  it("allows any tier at or below the clip's own", () => {
    expect(tierBlocked("private", "private")).toBe(false);
    expect(tierBlocked("private", "followers")).toBe(false);
    expect(tierBlocked("followers", "followers")).toBe(false);
    expect(tierBlocked("followers", "public")).toBe(false);
    expect(tierBlocked("public", "public")).toBe(false);
  });

  it("blocks anything wider than the clip", () => {
    expect(tierBlocked("followers", "private")).toBe(true);
    expect(tierBlocked("public", "private")).toBe(true);
    expect(tierBlocked("public", "followers")).toBe(true);
  });

  it("treats a missing ceiling as private, not as unrestricted", () => {
    // The fail-closed direction. A clip payload from a path that hasn't been
    // taught to resolve the ceiling must offer less than it could, never more —
    // the opposite default would show "Everyone" on a private clip.
    expect(tierBlocked("private", undefined)).toBe(false);
    expect(tierBlocked("followers", undefined)).toBe(true);
    expect(tierBlocked("public", undefined)).toBe(true);
  });

  it("leaves at least one option open for every possible ceiling", () => {
    // A composer with nothing selectable is a worse dead end than the 409 it
    // replaced. `private` is always available because a post can always be
    // narrower than its clip.
    for (const ceiling of TIERS) {
      expect(TIERS.some((t) => !tierBlocked(t, ceiling))).toBe(true);
      expect(tierBlocked("private", ceiling)).toBe(false);
    }
  });
});
