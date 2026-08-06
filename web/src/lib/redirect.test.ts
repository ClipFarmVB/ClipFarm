import { describe, expect, it } from "vitest";
import { DEFAULT_NEXT, safeNextPath } from "./redirect";

/** Where the browser would actually end up, resolved the way Next resolves hrefs. */
const APP = "https://app.clipfarm.test";
const landsOn = (path: string) => new URL(path, APP).origin;

const BS = String.fromCharCode(92); // a literal backslash, kept obvious in the source

describe("safeNextPath", () => {
  it.each([
    "/collections",
    "/games/abc?tab=clips#t=3",
    "/games?q=a&b=c",
    "/games#top",
    "/a//b",
    "/",
  ])("passes the same-origin path %j through untouched", (path) => {
    expect(safeNextPath(path)).toBe(path);
  });

  it.each([null, undefined, ""])("falls back to the default for %j", (value) => {
    expect(safeNextPath(value)).toBe(DEFAULT_NEXT);
  });

  // Each family here is a bug that shipped or nearly shipped in CF-153 (#156).
  describe("rejects destinations that leave the origin", () => {
    it.each([
      // Plain absolute and protocol-relative.
      "https://evil.com",
      "//evil.com",
      "//evil.com/games",
      "javascript:alert(1)",
      // Backslashes: the parser folds these into the leading `//`, which is why
      // a startsWith("//") check on the *raw input* is not enough.
      "/" + BS + "evil.com",
      BS + "/evil.com",
      // An authority inside the path. The first guard accepts these — the
      // resolved origin really is ours — and the surviving pathname is `//evil.com`.
      "//redirect.invalid//evil.com",
      "https://redirect.invalid//evil.com",
      "HTTPS://redirect.invalid//evil.com",
      "//redirect.invalid" + BS + BS + "evil.com",
      "//redirect.invalid/" + BS + BS + "evil.com",
      "//redirect.invalid//evil.com@real.test",
      "https://redirect.invalid///evil.com",
      // Nested authorities: one round of re-resolving strips a single level, so
      // this family is what makes the prefix check on the assembled path the
      // right guard rather than a second `new URL`.
      "//redirect.invalid//redirect.invalid//evil.com",
      "//redirect.invalid//redirect.invalid//redirect.invalid//evil.com",
    ])("%j", (next) => {
      expect(safeNextPath(next)).toBe(DEFAULT_NEXT);
      expect(landsOn(safeNextPath(next))).toBe(APP);
    });
  });

  it("never returns something that resolves off-origin", () => {
    const hosts = ["redirect.invalid", "evil.com", "127.0.0.1:3111", "evil.com@real.test"];
    const separators = ["//", "/" + BS, BS + BS, BS + "/", "///"];
    const schemes = ["", "https:", "HTTPS:", "//"];

    for (const scheme of schemes) {
      for (const first of separators) {
        for (const hostA of hosts) {
          for (const second of separators) {
            for (const hostB of hosts) {
              const next = `${scheme}${first}${hostA}${second}${hostB}/games`;
              expect(landsOn(safeNextPath(next)), `next=${JSON.stringify(next)}`).toBe(APP);
            }
          }
        }
      }
    }
  });

  it("keeps query and hash on a legitimate deep link", () => {
    expect(safeNextPath("/games/abc?tab=clips#t=3")).toBe("/games/abc?tab=clips#t=3");
  });
});
