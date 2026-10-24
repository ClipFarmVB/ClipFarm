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

function rootLayout(): string {
  return readFileSync(join(appDir, "_layout.tsx"), "utf8");
}

/**
 * The `name`s declared inside the `Stack.Protected` block with this guard.
 *
 * Scoped to the block rather than matched across the whole file, because where
 * a declaration sits is the thing that matters. expo-router registers a route
 * whose file exists whether the layout mentions it or not, and anything outside
 * both blocks is reachable while signed out — so "mentioned somewhere in
 * _layout.tsx" is satisfied by a screen that has been moved out of its guard,
 * and by the guards being deleted outright. Both of those passed a whole-file
 * match; neither passes this.
 */
function namesGuardedBy(guard: string): string[] {
  const blocks = [
    ...rootLayout().matchAll(
      /<Stack\.Protected\s+guard=\{([^}]*)\}>([\s\S]*?)<\/Stack\.Protected>/g
    ),
  ];
  return blocks
    .filter(([, blockGuard]) => blockGuard.trim() === guard)
    .flatMap(([, , inner]) => [...inner.matchAll(/name="([^"]+)"/g)].map((m) => m[1]));
}

describe("epic routes", () => {
  it("looks in the right place", () => {
    expect(existsSync(join(appDir, "_layout.tsx"))).toBe(true);
  });

  it.each(EPIC_SCREENS)("%s resolves to a screen", (_ticket, file) => {
    expect(existsSync(join(appDir, file)), `${file} is missing`).toBe(true);
  });

  it.each(EPIC_SCREENS)(
    "%s is declared inside the guard it belongs to",
    (_ticket, _file, declaredAs) => {
      // `(auth)` is the signed-out half; everything else in the epic is behind
      // a session. A screen in the wrong block — or in neither, because the
      // guards were removed — is reachable by whoever should not reach it.
      const guard = declaredAs === "(auth)" ? "!session" : "!!session";
      expect(
        namesGuardedBy(guard),
        `${declaredAs} is not declared inside <Stack.Protected guard={${guard}}>, ` +
          `so it is not behind that guard`
      ).toContain(declaredAs);
    }
  );

  it("binds the api client", () => {
    // `src/lib/api.ts` is the only caller of `configureApiClient`, and it does
    // it as an import side effect — so if nothing imports it, the client is
    // never configured and the first request throws. A static check because the
    // module reaches supabase-js and expo-secure-store, which do not load under
    // this runner; what it guards is that the import line still exists.
    expect(rootLayout()).toMatch(/import\s+"@\/lib\/api"/);
  });
});
