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
//
// CF-300 (#350) adds the page's own lifecycle: every page of clips, not the
// route's first 50; a poll that neither outlives the page nor rejects
// unhandled; a superseded filter load that cannot overwrite the newer one;
// and a header count that follows a delete.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getGame = vi.hoisted(() => vi.fn());
const getClips = vi.hoisted(() => vi.fn());
const getPlayers = vi.hoisted(() => vi.fn());
const deleteClips = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  getGame,
  getClips,
  getPlayers,
  deleteClips,
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
import { ApiError } from "@/lib/apiError";

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

// Mid-pipeline, so the page polls `getGame` every POLL_MS and fetches no clips.
const processingGame = {
  ...game,
  status: "processing",
  clip_count: null,
  progress: 0.4,
  progress_stage: "tracking_ball",
} as unknown as Game;

// The page's poll period.
const POLL_MS = 5000;

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
  // Reset, not clear: a test that queues more `mockResolvedValueOnce` pages
  // than the page asks for must not hand the leftovers to the next test.
  vi.resetAllMocks();
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
      page: 1,
      page_size: 100,
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

/** `n` distinct clips, ids `${prefix}0`…, for a page of the paged fetch. */
function clipPage(prefix: string, n: number): Clip[] {
  return Array.from({ length: n }, (_, i) => clip(`${prefix}${i}`));
}

function renderedClipIds() {
  return [...host.querySelectorAll("[data-clip]")].map((el) => el.getAttribute("data-clip"));
}

describe("loading every clip, not the route's first page (CF-300)", () => {
  it("pages through the route until a short page, and renders all of them", async () => {
    getClips
      .mockResolvedValueOnce(clipPage("a", 100))
      .mockResolvedValueOnce(clipPage("b", 30));
    await render();
    await act(async () => {});

    expect(getClips).toHaveBeenCalledTimes(2);
    expect(getClips.mock.calls[0][1]).toMatchObject({ page: 1, page_size: 100 });
    expect(getClips.mock.calls[1][1]).toMatchObject({ page: 2, page_size: 100 });
    expect(host.querySelectorAll("[data-clip]")).toHaveLength(130);
  });

  it("shows a clip that straddles two pages once", async () => {
    const first = clipPage("a", 100);
    getClips
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce([first[99], clip("b0")]);
    await render();
    await act(async () => {});

    const ids = renderedClipIds();
    expect(ids).toHaveLength(101);
    expect(new Set(ids).size).toBe(101);
    expect(ids[ids.length - 1]).toBe("b0");
  });

  // Each sequential page is one more promise turn.
  async function flushPages() {
    for (let i = 0; i < 15; i++) await act(async () => {});
  }

  function queueFullPages(n: number) {
    for (let i = 0; i < n; i++) getClips.mockResolvedValueOnce(clipPage(`p${i}-`, 100));
  }

  it("stops at ten pages, and warns only when a clip lies past them", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    queueFullPages(10);
    getClips.mockResolvedValueOnce([clip("past-cap")]);
    await render();
    await flushPages();

    expect(getClips).toHaveBeenCalledTimes(11);
    expect(getClips.mock.calls[9][1]).toMatchObject({ page: 10, page_size: 100 });
    // The probe: one clip at offset 1,000.
    expect(getClips.mock.calls[10][1]).toMatchObject({ page: 1001, page_size: 1 });
    expect(renderedClipIds()).toHaveLength(1000);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("stopped at 10 pages"));
    warn.mockRestore();
  });

  it("does not warn at exactly 1,000 clips, where nothing was cut", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    queueFullPages(10);
    getClips.mockResolvedValueOnce([]);
    await render();
    await flushPages();

    expect(getClips).toHaveBeenCalledTimes(11);
    expect(renderedClipIds()).toHaveLength(1000);
    expect(warn).not.toHaveBeenCalledWith(expect.stringContaining("stopped at"));
    warn.mockRestore();
  });

  // A superseded load must not spend more of the rate limit on pages nobody
  // will see.
  it("requests no further page for a load that newer filters replaced", async () => {
    await render();
    const releaseOlder = holdNextClips();
    await choosePlayer("p1");
    getClips.mockResolvedValue([]);
    await choosePlayer("p2");

    await releaseOlder(clipPage("a", 100));
    await flushPages();

    const olderPages = getClips.mock.calls
      .map((c) => c[1] as Record<string, unknown>)
      .filter((f) => f.player_id === "p1")
      .map((f) => f.page);
    expect(olderPages).toEqual([1]);
    expect(renderedClipIds()).toEqual([]);
  });
});

/** Advances one poll period and lets the tick's request land. */
async function tick() {
  await act(async () => {
    vi.advanceTimersByTime(POLL_MS);
  });
}

describe("the status poll (CF-300)", () => {
  it("starts no poll when the page is gone before the first fetch resolves", async () => {
    let resolveGame!: (g: Game) => void;
    getGame.mockReturnValueOnce(new Promise<Game>((r) => { resolveGame = r; }));
    await act(async () => {
      root.render(<GamePage />);
    });

    act(() => root.unmount());
    await act(async () => { resolveGame(processingGame); });
    await act(async () => {
      vi.advanceTimersByTime(POLL_MS * 3);
    });

    expect(getGame).toHaveBeenCalledTimes(1);
  });

  it("shows \"Game not found\" and stops polling when a tick 404s", async () => {
    getGame
      .mockResolvedValueOnce(processingGame)
      .mockRejectedValueOnce(new ApiError("Game not found", 404));
    await render();
    expect(host.textContent).toContain("Semifinal vs Ridgeview");

    await act(async () => {
      vi.advanceTimersByTime(POLL_MS);
    });

    expect(host.textContent).toContain("Game not found");
    expect(getGame).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(POLL_MS * 3);
    });

    expect(getGame).toHaveBeenCalledTimes(2);
  });

  // A blip, a 502 during a deploy or a 429 is not the end of the game: the
  // processing view stays and the next tick picks the poll back up.
  it("keeps the processing view and the poll through a tick that fails otherwise", async () => {
    getGame
      .mockResolvedValueOnce(processingGame)
      .mockRejectedValueOnce(new ApiError("Server error", 502))
      .mockResolvedValueOnce({ ...processingGame, progress: 0.7 });
    await render();

    await tick();

    expect(host.textContent).not.toContain("Server error");
    expect(host.textContent).not.toContain("Game not found");
    expect(host.textContent).toContain("Tracking the ball");
    expect(host.textContent).toContain("40%");

    await tick();

    expect(getGame).toHaveBeenCalledTimes(3);
    expect(host.textContent).toContain("70%");
  });

  it("says it cannot reach the server after three failed ticks in a row, and clears it on the next good one", async () => {
    getGame
      .mockResolvedValueOnce(processingGame)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(processingGame);
    await render();

    await tick();
    await tick();
    expect(host.textContent).not.toContain("Can't reach ClipFarm");

    await tick();
    expect(host.textContent).toContain("Can't reach ClipFarm right now.");
    expect(host.textContent).toContain("Tracking the ball");

    await tick();
    expect(getGame).toHaveBeenCalledTimes(5);
    expect(host.textContent).not.toContain("Can't reach ClipFarm");
  });

  // Ticks do not wait for each other. One slower than the period can land
  // after a later tick saw `ready` and stopped the poll.
  it("ignores a tick that lands after the poll stopped", async () => {
    let resolveSlow!: (g: Game) => void;
    getGame
      .mockResolvedValueOnce(processingGame)
      .mockReturnValueOnce(new Promise<Game>((r) => { resolveSlow = r; }))
      .mockResolvedValueOnce(game);
    await render();

    await tick();
    await tick();
    await act(async () => {});
    expect(renderedClipIds()).toEqual(["c1", "c2"]);

    await act(async () => { resolveSlow(processingGame); });
    await act(async () => {});

    expect(host.textContent).not.toContain("Tracking the ball");
    expect(renderedClipIds()).toEqual(["c1", "c2"]);
  });
});

describe("a superseded filter load (CF-300)", () => {
  it("keeps the newer result when the older one lands last", async () => {
    await render();
    const releaseOlder = holdNextClips();
    await choosePlayer("p1");
    const releaseNewer = holdNextClips();
    await choosePlayer("p2");

    await releaseNewer([clip("c2", players[1])]);
    await releaseOlder([clip("c1", players[0])]);

    expect(renderedClipIds()).toEqual(["c2"]);
  });

  it("does not flash the empty state when the older load lands empty first", async () => {
    await render();
    getClips.mockResolvedValue([]);
    await choosePlayer("p2");
    expect(host.textContent).toContain("No clips tagged Lena Brandt in this game.");

    const releaseOlder = holdNextClips();
    await choosePlayer("p1");
    const releaseNewer = holdNextClips();
    await choosePlayer("");

    await releaseOlder([]);

    expect(host.textContent).not.toContain("No clips tagged");
    expect(host.textContent).not.toContain("No clips match your filters.");
    expect(skeletons()).toBeGreaterThan(0);

    await releaseNewer([clip("c1", players[0]), clip("c2")]);

    expect(skeletons()).toBe(0);
    expect(renderedClipIds()).toEqual(["c1", "c2"]);
  });
});

describe("the header count after a delete (CF-300)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("drops by what the api deleted, without refetching the grid", async () => {
    getGame.mockResolvedValue({ ...game, clip_count: 5 });
    deleteClips.mockResolvedValue({ deleted: 2 });
    vi.stubGlobal("confirm", vi.fn(() => true));
    await render();
    expect(host.textContent).toContain("5 clips");
    const clipFetches = getClips.mock.calls.length;

    await clickButton("Select");
    await clickButton("All (2)");
    await clickButton("Delete 2");

    expect(deleteClips).toHaveBeenCalledWith(["c1", "c2"]);
    expect(host.textContent).toContain("3 clips");
    expect(host.textContent).not.toContain("5 clips");
    expect(getClips).toHaveBeenCalledTimes(clipFetches);
  });
});
