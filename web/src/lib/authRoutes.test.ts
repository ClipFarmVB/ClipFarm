import { describe, expect, it } from "vitest";
import { authRedirect } from "./authRoutes";

describe("authRedirect", () => {
  it("lets a signed-in visitor see the landing page too", () => {
    // CF-574: the logo links to `/` for everyone, so redirecting a signed-in
    // visitor off it would leave no way to reach it.
    expect(authRedirect("/", true)).toBeNull();
  });

  it("lets a signed-out visitor see the landing page", () => {
    expect(authRedirect("/", false)).toBeNull();
  });

  it.each(["/games", "/games/abc", "/upload", "/collections/1", "/settings/profile"])(
    "sends a signed-out visitor at %s to /login with next",
    (path) => {
      expect(authRedirect(path, false)).toBe(`/login?next=${encodeURIComponent(path)}`);
    },
  );

  it("does not treat a prefix lookalike as protected", () => {
    expect(authRedirect("/gamesx", false)).toBeNull();
  });

  it.each([true, false])("leaves public profiles alone (signed in: %s)", (signedIn) => {
    expect(authRedirect("/u/x", signedIn)).toBeNull();
  });

  it("lets a signed-in user through to the app", () => {
    expect(authRedirect("/games", true)).toBeNull();
    expect(authRedirect("/upload", true)).toBeNull();
  });
});
