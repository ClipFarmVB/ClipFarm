// @vitest-environment jsdom
//
// CF-250 (#271): `GET /clips` honoured `player_id` end to end and nothing on
// the game page set it, so tagging a clip with a player was half a feature —
// there was no way to then see that player's clips. The filter bar gains a
// player select, and the empty state names the player when the filter is
// what emptied the grid.
//
// Each acceptance criterion on the card has a test that fails without the
// control: select a player and the fetch narrows (combined with the other
// filters), clear it and the fetch widens, and a player with no clips gets a
// named empty state with a way out. Two more pin how the roster is fetched:
// never shown with nothing to pick, and a roster that fails to load does not
// take the grid down with it.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getGame = vi.hoisted(() => vi.fn());
const getClips = vi.hoisted(() => vi.fn());
const getPlayers = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  getGame,
  getClips,
  getPlayers,
  deleteClips: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "game-1" }) }));
vi.mock("next/link", () => ({
  default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
// The grid is what the page renders per clip; a marker per card is all these
// tests read from it.
vi.mock("@/components/ClipCard", () => ({
  ClipCard: ({ clip }: { clip: { id: string } }) => <div data-clip={clip.id} />,
}));
vi.mock("@/components/ClipModal", () => ({ ClipModal: () => null }));
vi.mock("@/components/CollectionPickerModal", () => ({ CollectionPickerModal: () => null }));

import GamePage from "@/app/games/[id]/page";
import type { Clip, Game, Player } from "@/lib/api";

// How long the filters must be still before the clips are refetched
// (CLIP_FILTER_DEBOUNCE_MS on the page).
const DEBOUNCE_MS = 300;

// `ready`, so the page fetches clips at once and starts no poll.
const game = {
  id: "game-1",
  title: "Semifinal vs Ridgeview",
  status: "ready",
  clip_count: 2,
  is_sample: false,
  created_at: "2026-10-04T18:00:00Z",
} as unknown as Game;

const players: Player[] = [
  { id: "p1", name: "Maya Okafor", jersey_number: 4, team_id: "t1", photo_url: null },
  { id: "p2", name: "Lena Brandt", jersey_number: null, team_id: "t1", photo_url: null },
];

function clip(id: string, player: Player | null = null): Clip {
  return {
    id,
    game_id: "game-1",
    player_id: player?.id ?? null,
    player_name: player?.name ?? null,
    action_type: "spike",
    confidence: 0.9,
    highlight_score: 0.6,
    start_time: 12,
    end_time: 19,
    clip_url: `https://example.test/${id}.mp4`,
    thumbnail_url: null,
    labels: [],
    created_at: "2026-10-04T18:20:00Z",
    source_available: true,
  } as unknown as Clip;
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  getGame.mockResolvedValue(game);
  getClips.mockResolvedValue([clip("c1", players[0]), clip("c2")]);
  getPlayers.mockResolvedValue(players);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.clearAllMocks();
});

/** Mounts the page and lets the game, clips and roster fetches resolve. */
async function render() {
  await act(async () => {
    root.render(<GamePage />);
  });
  // The game resolving schedules the clips and roster effects; one more turn
  // lets those promises land.
  await act(async () => {});
}

/** Lets the debounced filters settle and the fetch they trigger resolve. */
async function settle() {
  await act(async () => {
    vi.advanceTimersByTime(DEBOUNCE_MS);
  });
  await act(async () => {});
}

function playerSelect(): HTMLSelectElement | null {
  return host.querySelector('select[aria-label="Filter by player"]');
}

async function choosePlayer(value: string) {
  const select = playerSelect();
  if (!select) throw new Error("no player select");
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
}

function clickButton(label: string) {
  const btn = [...host.querySelectorAll("button")].find((b) => b.textContent?.trim() === label);
  if (!btn) throw new Error(`no ${label} button`);
  return act(async () => {
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

/**
 * Moves the nth range input in the bar (0 = Highlights, 1 = Confidence). The
 * value goes through the prototype's setter: React's value tracker wraps the
 * element's own, so a plain `.value =` is recorded as already-known and the
 * following event is dropped as a no-op.
 */
async function moveSlider(index: number, value: number) {
  const input = host.querySelectorAll<HTMLInputElement>('input[type="range"]')[index];
  if (!input) throw new Error(`no range input ${index}`);
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    set.call(input, String(value));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle();
}

/** The next `getClips` stays pending until the returned function is called. */
function holdNextClips() {
  let resolve!: (clips: Clip[]) => void;
  getClips.mockReturnValueOnce(new Promise<Clip[]>((r) => { resolve = r; }));
  return (clips: Clip[]) => act(async () => { resolve(clips); });
}

function lastClipsFilters() {
  const call = getClips.mock.calls[getClips.mock.calls.length - 1];
  return call[1] as Record<string, unknown>;
}

function skeletons() {
  return host.querySelectorAll(".skeleton").length;
}

describe("filtering the grid by player (CF-250)", () => {
  it("offers every player in the roster, in ClipCard's `#jersey name` form, behind an explicit clear", async () => {
    await render();

    const options = [...(playerSelect()?.options ?? [])].map((o) => [o.value, o.textContent]);
    expect(options).toEqual([
      ["", "All players"],
      ["p1", "#4 Maya Okafor"],
      ["p2", "Lena Brandt"],
    ]);
  });

  it("narrows the fetch to the chosen player and keeps the other filters as they were", async () => {
    await render();
    // Sort by score first, so the assertion sees a non-default filter survive
    // the player change rather than the initial state coming back by accident.
    await clickButton("Timeline");
    await settle();
    expect(lastClipsFilters()).toMatchObject({ sort: "score" });
    expect(lastClipsFilters().player_id).toBeUndefined();

    await choosePlayer("p1");

    expect(lastClipsFilters()).toEqual({
      player_id: "p1",
      sort: "score",
      min_confidence: 0,
      min_score: 0,
    });
    // The roster rides on the game, not the filters: two filter changes, one
    // roster fetch.
    expect(getPlayers).toHaveBeenCalledTimes(1);
  });

  it("widens the fetch again on \"All players\"", async () => {
    await render();
    await choosePlayer("p1");
    expect(lastClipsFilters().player_id).toBe("p1");

    await choosePlayer("");

    expect(lastClipsFilters().player_id).toBeUndefined();
    expect(playerSelect()?.value).toBe("");
  });

  it("names the player when the filter emptied the grid, and the way out refetches without it", async () => {
    await render();
    getClips.mockResolvedValue([]);

    await choosePlayer("p2");

    expect(host.querySelectorAll("[data-clip]")).toHaveLength(0);
    expect(host.textContent).toContain("No clips tagged Lena Brandt in this game.");
    expect(host.textContent).not.toContain("No clips match your filters.");

    getClips.mockResolvedValue([clip("c1", players[0]), clip("c2")]);
    await clickButton("Show all players");
    await settle();

    expect(lastClipsFilters().player_id).toBeUndefined();
    expect(host.querySelectorAll("[data-clip]")).toHaveLength(2);
    expect(playerSelect()?.value).toBe("");
  });

  // The two in-flight windows. `clips` is still the old `[]` and the first
  // load is long over, so without a flag for the pending refetch the empty
  // state would describe a result that has not landed: the game's clips are
  // on their way, and the page would say the game has none that match.
  it("shows the skeletons, not a false empty line, while \"Show all players\" refetches", async () => {
    await render();
    getClips.mockResolvedValue([]);
    await choosePlayer("p2");
    expect(host.textContent).toContain("No clips tagged Lena Brandt in this game.");

    const release = holdNextClips();
    await clickButton("Show all players");
    await settle();

    expect(lastClipsFilters().player_id).toBeUndefined();
    expect(host.textContent).not.toContain("No clips match your filters.");
    expect(host.textContent).not.toContain("No clips tagged");
    expect(skeletons()).toBeGreaterThan(0);

    await release([clip("c1", players[0]), clip("c2")]);

    expect(skeletons()).toBe(0);
    expect(host.querySelectorAll("[data-clip]")).toHaveLength(2);
  });

  it("names no player while a switch from an empty player to another is pending", async () => {
    await render();
    getClips.mockResolvedValue([]);
    await choosePlayer("p2");
    expect(host.textContent).toContain("No clips tagged Lena Brandt in this game.");

    const release = holdNextClips();
    await choosePlayer("p1");

    expect(lastClipsFilters().player_id).toBe("p1");
    // The option list still carries "#4 Maya Okafor"; the sentence must not.
    expect(host.textContent).not.toContain("No clips tagged");
    expect(skeletons()).toBeGreaterThan(0);

    await release([clip("c1", players[0])]);

    expect(skeletons()).toBe(0);
    expect(host.querySelectorAll("[data-clip]")).toHaveLength(1);
  });

  it("blames the other filters, not the game, when they are what is narrowing", async () => {
    await render();
    await clickButton("Timeline");
    getClips.mockResolvedValue([]);

    await choosePlayer("p2");

    // Sort alone is not a narrowing filter; the sliders and action badges are.
    expect(host.textContent).toContain("No clips tagged Lena Brandt in this game.");

    // A slider alone is one half of the branch.
    await moveSlider(0, 50);

    expect(lastClipsFilters()).toMatchObject({ min_score: 0.5 });
    expect(host.textContent).toContain("No clips tagged Lena Brandt match your other filters.");

    await moveSlider(0, 0);

    expect(host.textContent).toContain("No clips tagged Lena Brandt in this game.");

    // An action badge alone is the other half.
    await clickButton("spike");
    await settle();

    expect(host.textContent).toContain("No clips tagged Lena Brandt match your other filters.");

    // Every badge on is still a non-empty list the API applies as a filter
    // (it excludes `unknown`-typed clips), so the copy keeps blaming the
    // filters rather than the game.
    for (const action of ["serve", "dig", "set", "block"]) await clickButton(action);
    await settle();

    expect(lastClipsFilters()).toMatchObject({ action_type: ["spike", "serve", "dig", "set", "block"] });
    expect(host.textContent).toContain("No clips tagged Lena Brandt match your other filters.");
  });
});

describe("the roster behind the player filter (CF-250)", () => {
  it("shows no player control when the roster is empty", async () => {
    getPlayers.mockResolvedValue([]);
    await render();

    expect(playerSelect()).toBeNull();
    // The rest of the bar is untouched.
    expect(host.textContent).toContain("Timeline");
    expect(host.querySelectorAll("[data-clip]")).toHaveLength(2);
  });

  it("keeps the grid when the roster fails to load", async () => {
    getPlayers.mockRejectedValue(new Error("players: 503 Service Unavailable"));
    await render();

    expect(host.querySelectorAll("[data-clip]")).toHaveLength(2);
    expect(playerSelect()).toBeNull();
    expect(host.textContent).not.toContain("503");
    expect(host.textContent).toContain("Semifinal vs Ridgeview");
  });
});
