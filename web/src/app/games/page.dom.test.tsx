// @vitest-environment jsdom
//
// CF-304 (#354): "A failed fetch renders an error, never an affirmative 'you
// have none'." The empty-state body already carried `!error`; the header
// subtitle did not, so a failed games fetch put "No games yet" directly above
// the red error card.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchGames = vi.hoisted(() => vi.fn());
vi.mock("@/lib/gamesCache", () => ({
  fetchGames,
  getCachedGames: () => null,
  getInflightGames: () => null,
  updateGamesCache: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ deleteGame: vi.fn(), renameGame: vi.fn() }));
vi.mock("@/components/RequireAuth", () => ({
  RequireAuth: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import GamesPage from "@/app/games/page";

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
  vi.clearAllMocks();
});

describe("the games page", () => {
  it("does not say there are no games when the load failed", async () => {
    fetchGames.mockRejectedValue(new Error("Network down"));
    await act(async () => {
      root.render(<GamesPage />);
    });

    expect(host.textContent).toContain("Network down");
    expect(host.textContent).not.toContain("No games yet");
  });

  it("still says so when the load succeeded with none", async () => {
    fetchGames.mockResolvedValue([]);
    await act(async () => {
      root.render(<GamesPage />);
    });

    expect(host.textContent).toContain("No games yet");
  });
});
