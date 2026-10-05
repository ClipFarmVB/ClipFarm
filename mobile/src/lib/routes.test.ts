import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Every screen in the mobile epic (CF-313), and the file that will become it.
 *
 * This is a guard, not a routing table: expo-router derives routes from the
 * filesystem, so nothing reads this list at runtime and a screen ticket never
 * edits it. What it catches is a placeholder deleted or moved during a
 * rename — which would silently take a route with it and only surface as a
 * 404 in whichever other ticket linked to it.
 */
const EPIC_SCREENS: [ticket: string, file: string][] = [
  ["CF-328 (#378) sign in", "(auth)/login.tsx"],
  ["CF-328 (#378) sign up", "(auth)/signup.tsx"],
  ["CF-329 (#379) library", "(tabs)/games.tsx"],
  ["CF-335 (#385) collections", "(tabs)/collections.tsx"],
  ["CF-339 (#389) feed", "(tabs)/feed.tsx"],
  ["CF-338 (#388) own profile", "(tabs)/profile.tsx"],
  ["CF-330 (#380) upload", "upload.tsx"],
  ["CF-331 (#381) game detail", "games/[gameId].tsx"],
  ["CF-335 (#385) one collection", "collections/[collectionId].tsx"],
  ["CF-332 (#382) clip player", "clips/[clipId]/index.tsx"],
  ["CF-333 (#383) relabel and delete", "clips/[clipId]/edit.tsx"],
  ["CF-334 (#384) trim", "clips/[clipId]/trim.tsx"],
  ["CF-336 (#386) tag a player", "clips/[clipId]/tag.tsx"],
  ["CF-337 (#387) share", "clips/[clipId]/share.tsx"],
  ["CF-341 (#391) post to feed", "clips/[clipId]/post.tsx"],
  ["CF-338 (#388) a profile", "u/[handle]/index.tsx"],
  ["CF-340 (#390) followers", "u/[handle]/followers.tsx"],
  ["CF-340 (#390) following", "u/[handle]/following.tsx"],
  ["CF-343 (#393) settings", "settings/index.tsx"],
  ["CF-342 (#392) notification preferences", "settings/notifications.tsx"],
];

// Vitest runs from the project root. Asserted rather than assumed: a wrong
// root would otherwise fail every case below with "missing", which reads as a
// deleted screen rather than a misconfigured runner.
const appDir = join(process.cwd(), "src", "app");

describe("epic routes", () => {
  it("looks in the right place", () => {
    expect(existsSync(join(appDir, "_layout.tsx"))).toBe(true);
  });

  it.each(EPIC_SCREENS)("%s resolves to a screen", (_ticket, file) => {
    expect(existsSync(join(appDir, file)), `${file} is missing`).toBe(true);
  });
});
