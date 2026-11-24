import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { getRedirectUrl, unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { PROTECTED_PREFIXES } from "@/lib/authRoutes";

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

import { config, proxy } from "./proxy";

const APP = "https://app.clipfarm.test";
const request = (path: string) => new NextRequest(new URL(path, APP));

// Public routes the matcher must still run on (so a session refresh happens)
// but the proxy must never redirect.
const PUBLIC_PATHS = ["/login", "/.well-known/appspecific/com.chrome.devtools.json"];

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://supabase.test");
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "anon-test");
  currentUser = null;
  refreshedCookies = [];
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("proxy on /", () => {
  it("lets a signed-in user see the landing page", async () => {
    // CF-574: `/` used to redirect a signed-in user to /games, which left no
    // way back to it once the logo started linking there.
    currentUser = { id: "u1" };
    const res = await proxy(request("/"));

    expect(getRedirectUrl(res)).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });

  it("passes a signed-out visitor through to the landing page", async () => {
    const res = await proxy(request("/"));

    expect(getRedirectUrl(res)).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });
});

describe("proxy on protected routes", () => {
  it("still sends a signed-out visitor to /login with next", async () => {
    const res = await proxy(request("/games/abc"));

    expect(getRedirectUrl(res)).toBe(`${APP}/login?next=%2Fgames%2Fabc`);
  });

  // A redirect test, not a matcher test: calling proxy() directly bypasses
  // the matcher, which "proxy matcher" below covers for the same prefixes.
  it.each(PROTECTED_PREFIXES)("redirects a signed-out visitor on %s to /login", async (prefix) => {
    const res = await proxy(request(prefix));

    expect(getRedirectUrl(res)).toBe(`${APP}/login?next=${encodeURIComponent(prefix)}`);
  });

  it("carries cookies getUser() set or cleared across the /login redirect", async () => {
    // CF-567: a dead session is cleared by writing expired cookies through
    // setAll. A redirect is a fresh response, so without copying them across
    // the browser keeps presenting the stale session on the next request.
    refreshedCookies = [
      { name: "sb-access-token", value: "", options: { path: "/", httpOnly: true, maxAge: 0 } },
      { name: "sb-refresh-token", value: "fresh", options: { path: "/", httpOnly: true, sameSite: "lax" } },
    ];
    const res = await proxy(request("/games/abc"));

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
    const res = await proxy(request("/games"));

    expect(getRedirectUrl(res)).toBeNull();
  });
});

describe("proxy on public routes", () => {
  it.each(PUBLIC_PATHS)("passes a signed-out visitor through on %s", async (path) => {
    const res = await proxy(request(path));

    expect(getRedirectUrl(res)).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });
});

describe("proxy matcher", () => {
  it("runs on /, so a session refresh still happens there", () => {
    expect(unstable_doesMiddlewareMatch({ config, url: "/" })).toBe(true);
  });

  // The matcher is one hand-written regex, and a broken one compiles and
  // returns false — the route would then be served with no auth check at all
  // (CF-380). So the real matcher is held against every protected prefix.
  it.each(PROTECTED_PREFIXES)("runs on %s and everything under it", (prefix) => {
    expect(unstable_doesMiddlewareMatch({ config, url: prefix })).toBe(true);
    expect(unstable_doesMiddlewareMatch({ config, url: `${prefix}/x` })).toBe(true);
  });

  it.each(PUBLIC_PATHS)("runs on %s, which the proxy then lets through", (path) => {
    expect(unstable_doesMiddlewareMatch({ config, url: path })).toBe(true);
  });

  it("skips static assets and the Sentry tunnel", () => {
    expect(unstable_doesMiddlewareMatch({ config, url: "/monitoring" })).toBe(false);
    expect(unstable_doesMiddlewareMatch({ config, url: "/logo.svg" })).toBe(false);
  });
});
