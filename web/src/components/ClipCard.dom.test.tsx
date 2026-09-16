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
const updateClipLabels = vi.hoisted(() => vi.fn());
const trimClip = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  tagClip,
  updateClipLabels,
  trimClip,
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

  it("locks the select while the write is in flight, rather than dropping a second choice", async () => {
    // The guard in `handleTag` returns early on a second call, so without a
    // `disabled` on the control the UI accepts a change it then discards
    // silently — the defect this card exists to remove, one layer up.
    let resolve!: (c: Clip) => void;
    tagClip.mockReturnValue(new Promise<Clip>((r) => { resolve = r; }));
    await render();

    const select = await openTagSelect();
    await act(async () => {
      select.value = "p2";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(select.disabled).toBe(true);

    // **And the lock has to survive a blur.** In a browser, disabling the
    // focused element runs the HTML focus fixup rule and fires blur; this
    // select's `onBlur` closes the dropdown, so without a `tagLoading` check
    // there the control unmounts before `disabled` is ever painted. The
    // user-visible defect would still be closed — by the unmount — but every
    // sentence here claiming a lock would be false.
    //
    // jsdom implements no focus fixup, so this dispatches the event directly.
    // That tests the handler, which is the part this repo owns; it does not
    // and cannot test that a browser fires it.
    //
    // `focusout`, not `blur`. React's `onBlur` is delegated and listens for
    // the bubbling `focusout`; a non-bubbling `blur` never reaches it, so the
    // first version of this assertion passed against the unfixed component —
    // caught by reverting the guard and finding the suite still green.
    await act(async () => {
      select.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    });
    expect(host.querySelector("select")).not.toBeNull();

    await act(async () => {
      resolve({ ...clip, player_id: "p2", player_name: "Bob" });
    });
    // And it closes once the write is done.
    expect(host.querySelector("select")).toBeNull();
  });

  it("surfaces a failed tag, and does not tell the parent the write happened", async () => {
    // Named for what it pins. It does NOT pin that the dropdown stays open —
    // `setTagging(false)` is in the `finally`, so the select still closes on a
    // failure; what changed is that the reason now goes somewhere instead of
    // vanishing with it.
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

describe("the two mutations that failed into the console (CF-304)", () => {
  // `handleTag` was the loud one, so it was the one the card named. These two
  // were the same defect quieter: a rolled-back label reads as the UI refusing
  // the edit, and a failed trim showed nothing at all. Both wrote to
  // `console.error`, which is not a place a user looks.
  async function openPanel(name: string) {
    const btn = [...host.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === name,
    );
    if (!btn) throw new Error(`no ${name} button`);
    await act(async () => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }

  it("tells the user why a label snapped back", async () => {
    updateClipLabels.mockRejectedValue(new Error("Clip is no longer available"));
    const alert = vi.fn();
    vi.stubGlobal("alert", alert);
    await render();

    await openPanel("Label");
    const option = [...host.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "spike",
    );
    if (!option) throw new Error("no spike label");
    await act(async () => {
      option.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(updateClipLabels).toHaveBeenCalled();
    expect(alert).toHaveBeenCalledWith("Clip is no longer available");
    vi.unstubAllGlobals();
  });

  it("says a trim failed rather than silently keeping the old bounds", async () => {
    trimClip.mockRejectedValue(new Error("Source video expired"));
    const alert = vi.fn();
    vi.stubGlobal("alert", alert);
    await render();

    await openPanel("Trim");
    const minus = [...host.querySelectorAll("button")].find(
      (b) => b.textContent?.includes("2s"),
    );
    if (!minus) throw new Error("no trim control");
    await act(async () => {
      minus.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(trimClip).toHaveBeenCalled();
    expect(alert).toHaveBeenCalledWith("Source video expired");
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
