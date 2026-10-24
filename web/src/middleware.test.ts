import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { getRedirectUrl, unstable_doesMiddlewareMatch } from "next/experimental/testing/server";

// What the mocked Supabase client reports, and the cookies it "refreshes" —
// set per test.
let currentUser: { id: string } | null = null;
let refreshedCookies: { name: string; value: string; options: Record<string, unknown> }[] = [];

vi.mock("@supabase/ssr", () => ({
  createServerClient: (
    _url: string,
    _key: string,
    opts: { cookies: { setAll: (c: typeof refreshedCookies) => void } },
  ) => ({
    auth: {
      getUser: async () => {
        // A real refresh writes the new tokens through setAll before
        // getUser resolves; mirror that ordering.
        if (refreshedCookies.length) opts.cookies.setAll(refreshedCookies);
        return { data: { user: currentUser } };
      },
    },
  }),
}));

import { config, middleware } from "./middleware";

const APP = "https://app.clipfarm.test";
const request = (path: string) => new NextRequest(new URL(path, APP));

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://supabase.test");
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "anon-test");
  currentUser = null;
  refreshedCookies = [];
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("middleware on /", () => {
  it("lets a signed-in user see the landing page", async () => {
    // CF-574: `/` used to redirect a signed-in user to /games, which left no
    // way back to it once the logo started linking there.
    currentUser = { id: "u1" };
    const res = await middleware(request("/"));

    expect(getRedirectUrl(res)).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });

  it("passes a signed-out visitor through to the landing page", async () => {
    const res = await middleware(request("/"));

    expect(getRedirectUrl(res)).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });
});

describe("middleware on protected routes", () => {
  it("still sends a signed-out visitor to /login with next", async () => {
    const res = await middleware(request("/games/abc"));

    expect(getRedirectUrl(res)).toBe(`${APP}/login?next=%2Fgames%2Fabc`);
  });

  it("carries cookies getUser() set or cleared across the /login redirect", async () => {
    // CF-567: a dead session is cleared by writing expired cookies through
    // setAll. A redirect is a fresh response, so without copying them across
    // the browser keeps presenting the stale session on the next request.
    refreshedCookies = [
      { name: "sb-access-token", value: "", options: { path: "/", httpOnly: true, maxAge: 0 } },
      { name: "sb-refresh-token", value: "fresh", options: { path: "/", httpOnly: true, sameSite: "lax" } },
    ];
    const res = await middleware(request("/games/abc"));

    expect(getRedirectUrl(res)).toBe(`${APP}/login?next=%2Fgames%2Fabc`);
    const cleared = res.cookies.get("sb-access-token");
    expect(cleared?.value).toBe("");
    expect(cleared?.maxAge).toBe(0);
    const refreshed = res.cookies.get("sb-refresh-token");
    expect(refreshed?.value).toBe("fresh");
    expect(refreshed?.httpOnly).toBe(true);
    expect(refreshed?.sameSite).toBe("lax");
  });

  it("lets a signed-in user through", async () => {
    currentUser = { id: "u1" };
    const res = await middleware(request("/games"));

    expect(getRedirectUrl(res)).toBeNull();
  });
});

describe("middleware matcher", () => {
  it("runs on /, so a session refresh still happens there", () => {
    expect(unstable_doesMiddlewareMatch({ config, url: "/" })).toBe(true);
  });

  it("skips static assets and the Sentry tunnel", () => {
    expect(unstable_doesMiddlewareMatch({ config, url: "/monitoring" })).toBe(false);
    expect(unstable_doesMiddlewareMatch({ config, url: "/logo.svg" })).toBe(false);
  });
});
