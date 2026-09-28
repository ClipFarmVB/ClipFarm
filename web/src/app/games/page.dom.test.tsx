// @vitest-environment jsdom
//
// CF-221 R8-M1: the Library must not judge one user's games against another
// user's onboarding record. Another tab signing in as a different account
// changes `user` under a mounted Library; the list loaded for the previous
// user must not survive into the new user's panel.
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
vi.mock("@/lib/gamesCache", () => ({
  getCachedGames: () => cached,
  getInflightGames: () => inflight,
  fetchGames: () => inflight ?? new Promise<Game[]>(() => {}),
  updateGamesCache: () => {},
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
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  localStorage.clear();
  authUser = null;
  cached = null;
  inflight = null;
});

const panel = () => host.querySelector('section[aria-labelledby="onboarding-heading"]');

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
