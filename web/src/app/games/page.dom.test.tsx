// @vitest-environment jsdom
//
// Two suites share this file, and one set of module mocks:
//
// - CF-304 (#354): "A failed fetch renders an error, never an affirmative 'you
//   have none'." The empty-state body already carried `!error`; the header
//   subtitle did not, so a failed games fetch put "No games yet" directly above
//   the red error card.
// - CF-221 R8-M1: the Library must not judge one user's games against another
//   user's onboarding record. Another tab signing in as a different account
//   changes `user` under a mounted Library; the list loaded for the previous
//   user must not survive into the new user's panel.
// - CF-221 R10-M1: signing out under a mounted Library must not remount it into
//   an unauthenticated `fetchGames()`, whose 401 left a red error card.
//
// `fetchGames` is a single mock whose behaviour each test sets: by default it
// hands back the in-flight prefetch (or never settles), which is what the
// account-switch test drives; the CF-304 tests override it per test.
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Game } from "@/lib/api";
import { FALLBACK_UPLOAD_CONFIG } from "@/lib/uploadLimits";

let authUser: { id: string } | null = null;
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: authUser, session: null, loading: false, signOut: async () => {} }),
}));

let cached: Game[] | null = null;
let inflight: Promise<Game[]> | null = null;
const fetchGames = vi.hoisted(() => vi.fn());
vi.mock("@/lib/gamesCache", () => ({
  fetchGames,
  getCachedGames: () => cached,
  getInflightGames: () => inflight,
  updateGamesCache: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getUploadConfig: () => Promise.resolve(FALLBACK_UPLOAD_CONFIG),
  deleteGame: vi.fn(),
  renameGame: vi.fn(),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: ComponentProps<"a">) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

import GamesPage from "@/app/games/page";
import { onboardingKey, readOnboarding } from "@/lib/onboarding";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function game(id: string, status: Game["status"]): Game {
  return { id, title: id, status, progress: 0, progress_stage: null, created_at: "2026-01-01T00:00:00Z" };
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  fetchGames.mockImplementation(() => inflight ?? new Promise<Game[]>(() => {}));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
  localStorage.clear();
  authUser = null;
  cached = null;
  inflight = null;
});

describe("the games page", () => {
  it("does not say there are no games when the load failed", async () => {
    authUser = { id: "cf304" };
    fetchGames.mockRejectedValue(new Error("Network down"));
    await act(async () => {
      root.render(<GamesPage />);
    });

    expect(host.textContent).toContain("Network down");
    expect(host.textContent).not.toContain("No games yet");
  });

  it("still says so when the load succeeded with none", async () => {
    authUser = { id: "cf304" };
    fetchGames.mockResolvedValue([]);
    await act(async () => {
      root.render(<GamesPage />);
    });

    expect(host.textContent).toContain("No games yet");
  });
});

const panel = () => host.querySelector('section[aria-labelledby="onboarding-heading"]');

describe("Library with the demo game", () => {
  it("links the demo game from the onboarding panel as the worked example", async () => {
    authUser = { id: "demo-link" };
    fetchGames.mockResolvedValue([{ ...game("demo-copy", "ready"), is_sample: true }]);
    await act(async () => { root.render(<GamesPage />); });

    expect(panel()).not.toBeNull();
    const links = Array.from(host.querySelectorAll('a[href="/games/demo-copy"]'));
    expect(links.map((a) => a.textContent)).toContain("See an example");
  });

  it("shows no example link when the account has no demo game", async () => {
    // A game the api did not flag must not be linked as the example, so the
    // list is not empty: an empty one would pass however the id is chosen.
    authUser = { id: "demo-none" };
    fetchGames.mockResolvedValue([{ ...game("own-processing", "processing"), is_sample: false }]);
    await act(async () => { root.render(<GamesPage />); });

    expect(panel()).not.toBeNull();
    expect(host.textContent).not.toContain("See an example");
  });
});

describe("Library across an account switch", () => {
  it("does not mark the new user done from the previous user's games", async () => {
    // User A, with a ready game, has the Library open.
    authUser = { id: "switch-a" };
    cached = [game("a-ready", "ready")];
    await act(async () => { root.render(<GamesPage />); });
    expect(readOnboarding("switch-a")).toBe("done");

    // Another tab signs in as B. AuthContext clears the cache and starts B's
    // prefetch before the re-render.
    let resolveB!: (games: Game[]) => void;
    authUser = { id: "switch-b" };
    cached = null;
    inflight = new Promise<Game[]>((r) => { resolveB = r; });
    await act(async () => { root.render(<GamesPage />); });

    expect(localStorage.getItem(onboardingKey("switch-b"))).toBeNull();
    expect(readOnboarding("switch-b")).toBeNull();
    expect(host.textContent).not.toContain("a-ready");

    // B's own (empty) list arrives: B is a new user and sees the panel.
    await act(async () => { resolveB([]); });
    expect(readOnboarding("switch-b")).toBeNull();
    expect(panel()).not.toBeNull();
  });
});

describe("Library across a sign-out", () => {
  it("makes no request and shows no error once the user is signed out", async () => {
    authUser = { id: "signout-a" };
    cached = [game("a-ready", "ready")];
    await act(async () => { root.render(<GamesPage />); });
    expect(host.textContent).toContain("a-ready");

    // Sign-out: AuthContext clears the cache and, with no user, starts no
    // prefetch. An unauthenticated fetch here would 401.
    fetchGames.mockRejectedValue(new Error("401 Unauthorized"));
    authUser = null;
    cached = null;
    inflight = null;
    await act(async () => { root.render(<GamesPage />); });

    expect(fetchGames).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain("401");
    expect(host.textContent).not.toContain("a-ready");
  });
});
