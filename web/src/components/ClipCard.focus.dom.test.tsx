// @vitest-environment jsdom
//
// CF-273 (#308), the half that closes CF-227's outstanding criterion: closing
// the clip player returns focus to the card that opened it.
//
// The trap always tried — it captures activeElement on open and hands it back
// on close. What it lacked was something to hand back to: the thumbnail was a
// bare `<div onClick>`, so activeElement was <body>. This mounts the real card
// and the real modal together, the way the games and collections pages do,
// so the assertion is about the pair rather than about the hook in isolation.
//
// A separate file from ClipCard.dom.test.tsx because that suite's `@/lib/api`
// mock has no `getClipShareUrl` or `setClipVisibility`, which ClipModal imports.
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  tagClip: vi.fn(),
  updateClipLabels: vi.fn(),
  trimClip: vi.fn(),
  getClipDownloadUrl: vi.fn(),
  getClipShareUrl: vi.fn(),
  setClipVisibility: vi.fn(),
}));
vi.mock("@/lib/useMe", () => ({
  useMe: () => null,
  needsHandle: () => false,
}));
vi.mock("@/lib/download", () => ({ startCrossOriginDownload: vi.fn() }));
// Not under test, and the real one pulls in `createPost` and a trap of its own.
vi.mock("@/components/PostComposerModal", () => ({ PostComposerModal: () => null }));

import { ClipCard } from "@/components/ClipCard";
import { ClipModal } from "@/components/ClipModal";
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
  effective_visibility: "private",
} as unknown as Clip;

/** The grid page, reduced to one card and the modal it opens. */
function Grid() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <ClipCard clip={clip} players={[]} onPlay={() => setOpen(true)} />
      {open && <ClipModal clip={clip} onClose={() => setOpen(false)} />}
    </>
  );
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  HTMLMediaElement.prototype.load = vi.fn();
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
  // useBodyScrollLock restores the scroll position on release; jsdom has no
  // scrollTo and prints a "Not implemented" stack for every close.
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
});

function press(key: string) {
  act(() => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  });
}

describe("closing a clip returns focus to its card (CF-273)", () => {
  it("lands back on the play button that opened it", () => {
    act(() => root.render(<Grid />));
    const opener = host.querySelector<HTMLButtonElement>('button[aria-label="Play spike clip"]');
    if (!opener) throw new Error("no play button");
    // jsdom has no layout, so getClientRects() is empty and restoreFocusTo
    // declines every target as hidden. Same stub PostComposerModal's suite uses.
    opener.getClientRects = () => [{}] as unknown as DOMRectList;

    // Focused explicitly: jsdom does not move focus on a synthetic click, and a
    // keyboard user arrives here by Tab, which is the case this card is about.
    opener.focus();
    act(() => {
      opener.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // The precondition that makes the last line mean something: focus really
    // left the opener. Without it, "focus is on the opener at the end" holds
    // for a modal that never took focus at all.
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog!.contains(document.activeElement)).toBe(true);

    press("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("lands back on it after a click that did not focus it, as in Safari", () => {
    // WebKit does not focus a button on click or tap, and neither does jsdom
    // on a synthetic click, so a click with focus still on <body> is that
    // case. The card focuses its button itself before playing; without that,
    // the trap captures <body> and the last line fails.
    act(() => root.render(<Grid />));
    const opener = host.querySelector<HTMLButtonElement>('button[aria-label="Play spike clip"]');
    if (!opener) throw new Error("no play button");
    opener.getClientRects = () => [{}] as unknown as DOMRectList;

    expect(document.activeElement).toBe(document.body);
    act(() => {
      opener.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog!.contains(document.activeElement)).toBe(true);

    press("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});
