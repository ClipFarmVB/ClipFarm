import { existsSync, readFileSync } from "node:fs";
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
const EPIC_SCREENS: [ticket: string, file: string, declaredAs: string][] = [
  ["CF-328 (#378) sign in", "(auth)/login.tsx", "(auth)"],
  ["CF-328 (#378) sign up", "(auth)/signup.tsx", "(auth)"],
  ["CF-329 (#379) library", "(tabs)/games.tsx", "(tabs)"],
  ["CF-335 (#385) collections", "(tabs)/collections.tsx", "(tabs)"],
  ["CF-339 (#389) feed", "(tabs)/feed.tsx", "(tabs)"],
  ["CF-338 (#388) own profile", "(tabs)/profile.tsx", "(tabs)"],
  ["CF-330 (#380) upload", "upload.tsx", "upload"],
  ["CF-331 (#381) game detail", "games/[gameId].tsx", "games/[gameId]"],
  ["CF-335 (#385) one collection", "collections/[collectionId].tsx", "collections/[collectionId]"],
  ["CF-332 (#382) clip player", "clips/[clipId]/index.tsx", "clips/[clipId]/index"],
  ["CF-333 (#383) relabel and delete", "clips/[clipId]/edit.tsx", "clips/[clipId]/edit"],
  ["CF-334 (#384) trim", "clips/[clipId]/trim.tsx", "clips/[clipId]/trim"],
  ["CF-336 (#386) tag a player", "clips/[clipId]/tag.tsx", "clips/[clipId]/tag"],
  ["CF-337 (#387) share", "clips/[clipId]/share.tsx", "clips/[clipId]/share"],
  ["CF-341 (#391) post to feed", "clips/[clipId]/post.tsx", "clips/[clipId]/post"],
  ["CF-338 (#388) a profile", "u/[handle]/index.tsx", "u/[handle]/index"],
  ["CF-340 (#390) followers", "u/[handle]/followers.tsx", "u/[handle]/followers"],
  ["CF-340 (#390) following", "u/[handle]/following.tsx", "u/[handle]/following"],
  ["CF-343 (#393) settings", "settings/index.tsx", "settings/index"],
  ["CF-342 (#392) notification preferences", "settings/notifications.tsx", "settings/notifications"],
];

// Vitest runs from the project root. Asserted rather than assumed: a wrong
// root would otherwise fail every case below with "missing", which reads as a
// deleted screen rather than a misconfigured runner.
const appDir = join(process.cwd(), "src", "app");

/**
 * Every `name` the root layout declares.
 *
 * Checked as well as the files, because expo-router registers a route whose
 * file exists regardless of whether the layout mentions it — and anything it
 * does not mention sits outside both `Stack.Protected` blocks, reachable while
 * signed out. A misspelled `Stack.Screen name` is the way that happens by
 * accident: the typo'd entry guards nothing and the real screen is left open,
 * with no error anywhere.
 */
function declaredScreenNames(): string[] {
  const layout = readFileSync(join(appDir, "_layout.tsx"), "utf8");
  return [...layout.matchAll(/name="([^"]+)"/g)].map((match) => match[1]);
}

describe("epic routes", () => {
  it("looks in the right place", () => {
    expect(existsSync(join(appDir, "_layout.tsx"))).toBe(true);
  });

  it.each(EPIC_SCREENS)("%s resolves to a screen", (_ticket, file) => {
    expect(existsSync(join(appDir, file)), `${file} is missing`).toBe(true);
  });

  it.each(EPIC_SCREENS)("%s is declared in the root layout", (_ticket, _file, declaredAs) => {
    expect(
      declaredScreenNames(),
      `${declaredAs} is not declared in _layout.tsx, so it is not behind a guard`
    ).toContain(declaredAs);
  });
});
