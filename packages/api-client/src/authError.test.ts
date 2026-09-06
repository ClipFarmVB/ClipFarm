import { describe, expect, it } from "vitest";
import { authErrorMessage } from "./authError";

const GENERIC = "We couldn't confirm your email. Please try signing in.";

describe("authErrorMessage", () => {
  it("maps a known code to its message", () => {
    expect(authErrorMessage("link_expired")).toBe(
      "That confirmation link has expired or was already used."
    );
    expect(authErrorMessage("link_invalid")).toBe("That confirmation link is invalid.");
  });

  it.each([null, undefined, ""])("returns null for %j", (code) => {
    expect(authErrorMessage(code)).toBeNull();
  });

  it("falls back to the generic message for an unknown code", () => {
    expect(authErrorMessage("made_up")).toBe(GENERIC);
  });

  /**
   * Inherited keys reached `Object.prototype` before the own-property check
   * (CF-16, #158): `__proto__` returned an object, which React refuses to
   * render — a crafted link took the whole login page down — and the rest
   * returned functions, which React drops silently, so no banner appeared.
   */
  describe("prototype keys", () => {
    const inherited = [
      "__proto__",
      "constructor",
      "toString",
      "valueOf",
      "hasOwnProperty",
      "isPrototypeOf",
      "propertyIsEnumerable",
      "toLocaleString",
    ];

    it.each(inherited)("%s returns the generic message", (code) => {
      expect(authErrorMessage(code)).toBe(GENERIC);
    });

    it.each(inherited)("%s returns a string, never an object or function", (code) => {
      // The type signature says `string | null`; before the fix it lied.
      expect(typeof authErrorMessage(code)).toBe("string");
    });
  });
});
