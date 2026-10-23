// @vitest-environment jsdom
//
// CF-272 (#306): the card's action row held Download, Label, Save, Trim and the
// player tag on one line it could not wrap, inside a card that clips overflow,
// so Trim and the tag were cut off its right edge — unreachable, with nothing
// to say they were there. The fix splits the controls by when they are used:
// Download and Save stay here for triage; Label, Trim and the tag moved into
// ClipModal, whose tests (ClipModal.edit.dom.test.tsx) now carry the CF-304
// cases that used to live in this file.
//
// Targets the always-visible footer. The thumbnail overlay is
// `opacity-0 group-hover:opacity-100` and jsdom applies no `:hover` and does no
// layout, so any assertion about that chrome would pass against broken code.
// The clipping itself is a layout fact jsdom cannot measure; the rendered card
// was checked in a browser at 375px, and what is pinned here is the control set.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getClipDownloadUrl: vi.fn(),
}));

import { ClipCard } from "@/components/ClipCard";
import type { Clip } from "@/lib/api";

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

function footerLabels(): string[] {
  return [...host.querySelectorAll("button")]
    .map((b) => (b.textContent ?? "").trim())
    .filter(Boolean);
}

describe("the card's action row (CF-272)", () => {
  it("offers Download and Save, and none of the edits that moved to the modal", async () => {
    await act(async () => {
      root.render(<ClipCard clip={clip} onPlay={() => {}} onSave={() => {}} />);
    });

    expect(footerLabels()).toEqual(["Download", "Save"]);
    // The tag used to swap its button for a <select> in this row.
    expect(host.querySelector("select")).toBeNull();
  });

  it("is free to wrap rather than being held at its content width", async () => {
    // `shrink-0` is what made `flex-wrap` inert. A class assertion because
    // jsdom does no layout; the rendered card is the real check.
    await act(async () => {
      root.render(<ClipCard clip={clip} onPlay={() => {}} onSave={() => {}} />);
    });
    const row = [...host.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Save")!
      .parentElement!;

    expect(row.className).toContain("flex-wrap");
    expect(row.className).not.toContain("shrink-0");
  });

  it("shows the tagged player, as text rather than a control", async () => {
    await act(async () => {
      root.render(<ClipCard clip={{ ...clip, player_name: "Maya Okafor" }} onPlay={() => {}} />);
    });

    expect(host.textContent).toContain("Maya Okafor");
    expect(footerLabels().some((l) => l.includes("Maya Okafor"))).toBe(false);
  });

  it("renders from the row it is given, so an edit made in the modal reaches it", async () => {
    // The card used to copy the clip into `local*` state on mount, so a change
    // the page made to its row afterwards — which is now the only way the
    // card learns of a tag, label or trim — never showed.
    await act(async () => {
      root.render(<ClipCard clip={clip} onPlay={() => {}} />);
    });
    expect(host.textContent).not.toContain("Maya Okafor");

    await act(async () => {
      root.render(
        <ClipCard
          clip={{ ...clip, player_name: "Maya Okafor", labels: ["dig"], start_time: 2, end_time: 9 }}
          onPlay={() => {}}
        />,
      );
    });

    expect(host.textContent).toContain("Maya Okafor");
    expect(host.textContent).toContain("dig");
    expect(host.textContent).toContain("0:07"); // the new duration
  });
});

// CF-273 (#308): opening a clip used to be a bare `<div onClick>` with the
// selection checkbox nested inside it — no focus, no Enter/Space, so the grid's
// primary action had no keyboard path at all. It is now a sibling `<button>`
// covering the frame. These pin what that buys: a real, focusable control, no
// interactive element inside another, and select mode routing keyboard users
// to the checkbox alone. Focus coming back on close is in
// ClipCard.focus.dom.test.tsx, which needs a different `@/lib/api` mock.
describe("opening a clip from the grid (CF-273)", () => {
  async function renderCard(props: {
    onPlay?: (c: Clip) => void;
    onToggleSelect?: (id: string) => void;
  }) {
    await act(async () => {
      root.render(
        <ClipCard
          clip={clip}
          onPlay={props.onPlay ?? (() => {})}
          onToggleSelect={props.onToggleSelect}
        />,
      );
    });
  }

  function playButton(): HTMLButtonElement {
    const b = host.querySelector<HTMLButtonElement>('button[aria-label="Play spike clip"]');
    if (!b) throw new Error("no play button");
    return b;
  }

  async function click(el: Element) {
    await act(async () => {
      el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }

  it("is a focusable button that plays the clip once", async () => {
    const onPlay = vi.fn();
    await renderCard({ onPlay });
    const b = playButton();

    expect(b.type).toBe("button");
    expect(b.tabIndex).toBe(0);
    b.focus();
    expect(document.activeElement).toBe(b);

    await click(b);
    expect(onPlay).toHaveBeenCalledTimes(1);
    expect(onPlay).toHaveBeenCalledWith(clip);
  });

  it("carries a focus ring for keyboard focus", async () => {
    // jsdom does not evaluate :focus-visible, so the class is what can be
    // checked here; the ring itself was checked in the running app.
    await renderCard({});
    expect(playButton().className).toContain("focus-visible:outline-brand");
  });

  it("nests no interactive element inside another", async () => {
    // Both modes: select mode is the one that renders the checkbox, which is
    // what sat inside the old clickable div.
    await renderCard({});
    expect(host.querySelectorAll("button button, button a, [role=button] button")).toHaveLength(0);
    await renderCard({ onToggleSelect: () => {} });
    expect(host.querySelectorAll("button button, button a, [role=button] button")).toHaveLength(0);
  });

  it("in select mode, the checkbox toggles once and does not play", async () => {
    const onPlay = vi.fn();
    const onToggleSelect = vi.fn();
    await renderCard({ onPlay, onToggleSelect });
    const checkbox = host.querySelector<HTMLButtonElement>('button[aria-label="Select"]');
    if (!checkbox) throw new Error("no checkbox");

    expect(checkbox.type).toBe("button");
    await click(checkbox);
    expect(onToggleSelect).toHaveBeenCalledTimes(1);
    expect(onToggleSelect).toHaveBeenCalledWith("clip-1");
    expect(onPlay).not.toHaveBeenCalled();
  });

  it("in select mode, the frame toggles on click but is out of the tab order", async () => {
    // A mouse convenience, as the old div was. The checkbox is the keyboard
    // path; a second focusable control doing the same thing would be a
    // redundant tab stop and a duplicate for a screen reader.
    const onPlay = vi.fn();
    const onToggleSelect = vi.fn();
    await renderCard({ onPlay, onToggleSelect });
    expect(host.querySelector('button[aria-label^="Play"]')).toBeNull();
    const frame = host.querySelector<HTMLButtonElement>('button[aria-hidden="true"]');
    if (!frame) throw new Error("no frame button");

    expect(frame.tabIndex).toBe(-1);
    await click(frame);
    expect(onToggleSelect).toHaveBeenCalledTimes(1);
    expect(onPlay).not.toHaveBeenCalled();
  });

  it("in select mode, a mousedown on the frame cannot take focus", async () => {
    // tabIndex=-1 keeps it out of the tab order but a click would still focus
    // it, leaving focus on a hidden, unnamed button. Cancelling the mousedown
    // is what stops that in a browser; jsdom only reports it.
    await renderCard({ onToggleSelect: vi.fn() });
    const frame = host.querySelector<HTMLButtonElement>('button[aria-hidden="true"]');
    if (!frame) throw new Error("no frame button");
    const down = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
    act(() => {
      frame.dispatchEvent(down);
    });
    expect(down.defaultPrevented).toBe(true);
  });

  it("outside select mode, a mousedown on the play button is left alone", async () => {
    await renderCard({});
    const down = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
    act(() => {
      playButton().dispatchEvent(down);
    });
    expect(down.defaultPrevented).toBe(false);
  });
});
