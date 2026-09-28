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
  it("redirects a signed-in user to /games", async () => {
    currentUser = { id: "u1" };
    const res = await middleware(request("/"));

    expect(res.status).toBe(307);
    expect(getRedirectUrl(res)).toBe(`${APP}/games`);
  });

  it("passes a signed-out visitor through to the landing page", async () => {
    const res = await middleware(request("/"));

    expect(getRedirectUrl(res)).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });

  it("carries refreshed session cookies across the redirect", async () => {
    currentUser = { id: "u1" };
    refreshedCookies = [
      { name: "sb-access-token", value: "fresh", options: { path: "/", httpOnly: true } },
    ];
    const res = await middleware(request("/"));

    expect(getRedirectUrl(res)).toBe(`${APP}/games`);
    expect(res.cookies.get("sb-access-token")?.value).toBe("fresh");
  });
});

describe("middleware on protected routes", () => {
  it("still sends a signed-out visitor to /login with next", async () => {
    const res = await middleware(request("/games/abc"));

    expect(getRedirectUrl(res)).toBe(`${APP}/login?next=%2Fgames%2Fabc`);
  });

  it("lets a signed-in user through", async () => {
    currentUser = { id: "u1" };
    const res = await middleware(request("/games"));

    expect(getRedirectUrl(res)).toBeNull();
  });
});

describe("middleware matcher", () => {
  it("runs on /, so the signed-in redirect can fire", () => {
    expect(unstable_doesMiddlewareMatch({ config, url: "/" })).toBe(true);
  });

  it("skips static assets and the Sentry tunnel", () => {
    expect(unstable_doesMiddlewareMatch({ config, url: "/monitoring" })).toBe(false);
    expect(unstable_doesMiddlewareMatch({ config, url: "/logo.svg" })).toBe(false);
  });
});
