// @vitest-environment jsdom
//
// CF-304 items 3 and 8: tagging a player used to fail silently and update only
// the card's own state.
//
// `handleTag` closed the select BEFORE awaiting `tagClip`, had no try/catch,
// and set only `localPlayerName`. So a failure vanished — dropdown shut, no
// message, tag not applied — and on success the parent's `clips[]` kept the
// stale `player_name`, which is what `ClipModal` reads, so the modal for the
// same clip showed no player.
//
// The fix is `onUpdate?.(updated)`: the prop already exists and both sibling
// mutations (`handleToggleLabel`, `handleTrim`) already use it, and both call
// sites already pass a handler that splices the clip into the parent array.
//
// Targets the always-visible footer. The thumbnail overlay is
// `opacity-0 group-hover:opacity-100` and jsdom applies no `:hover` and does no
// layout, so any assertion about that chrome would pass against broken code.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tagClip = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  tagClip,
  updateClipLabels: vi.fn(),
  trimClip: vi.fn(),
  getClipDownloadUrl: vi.fn(),
}));

import { ClipCard } from "@/components/ClipCard";
import type { Clip, Player } from "@/lib/api";

const clip = {
  id: "clip-1",
  game_id: "game-1",
  player_id: null,
  player_name: null,
  action_type: "spike",
  confidence: 0.9,
  highlight_score: null,
  start_time: 0,
  end_time: 5,
  clip_url: "https://example.test/c.mp4",
  thumbnail_url: "https://example.test/c.jpg",
  labels: [],
  created_at: "2026-01-01T00:00:00Z",
  source_available: true,
} as unknown as Clip;

const players: Player[] = [
  { id: "p1", name: "Alice", jersey_number: null, team_id: null, photo_url: null },
  { id: "p2", name: "Bob", jersey_number: 7, team_id: null, photo_url: null },
];

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

async function render(onUpdate?: (c: Clip) => void) {
  await act(async () => {
    root.render(
      <ClipCard clip={clip} players={players} onPlay={() => {}} onUpdate={onUpdate} />,
    );
  });
}

function tagButton() {
  return [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Tag"));
}

async function openTagSelect() {
  const btn = tagButton();
  if (!btn) throw new Error("no Tag button");
  await act(async () => {
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  const select = host.querySelector("select");
  if (!select) throw new Error("select did not open");
  return select;
}

describe("tagging a player (CF-304)", () => {
  it("propagates the tagged clip to the parent, not just its own state", async () => {
    const tagged = { ...clip, player_id: "p2", player_name: "Bob" };
    tagClip.mockResolvedValue(tagged);
    const onUpdate = vi.fn();
    await render(onUpdate);

    const select = await openTagSelect();
    await act(async () => {
      select.value = "p2";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(tagClip).toHaveBeenCalledWith("clip-1", "p2");
    expect(onUpdate).toHaveBeenCalledWith(tagged);
  });

  it("surfaces a failure instead of closing the dropdown on it", async () => {
    tagClip.mockRejectedValue(new Error("Clip is no longer available"));
    const alert = vi.fn();
    vi.stubGlobal("alert", alert);
    const onUpdate = vi.fn();
    await render(onUpdate);

    const select = await openTagSelect();
    await act(async () => {
      select.value = "p1";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(alert).toHaveBeenCalledWith("Clip is no longer available");
    expect(onUpdate).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("a player with no jersey number (CF-304)", () => {
  it("renders the name with no stray hash in front of it", async () => {
    // `jersey_number` is nullable in the API schema and the backend explicitly
    // orders `nullslast`, so this is a shipped row shape, not a hypothetical.
    //
    // **The card says this renders "#null Alice". It does not** — React renders
    // `null` as nothing, so `#{p.jersey_number} {p.name}` produced "# Alice", a
    // hash with no number. Asserting the absence of "#null" therefore passes
    // against the BROKEN code, which is how the first version of this test was
    // written and what its own revert-check caught. Asserting the option's
    // exact text is what can actually fail.
    await render();
    const select = await openTagSelect();
    const options = [...select.querySelectorAll("option")].map((o) => o.textContent);

    expect(options).toContain("Alice");
    expect(options).toContain("#7 Bob");
  });
});
