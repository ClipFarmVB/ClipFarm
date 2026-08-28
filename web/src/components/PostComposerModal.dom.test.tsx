// @vitest-environment jsdom
//
// CF-109: the composer is a nested overlay, and has to behave like one.
//
// The regression this pins: it rendered no focus trap of its own, so ClipModal
// stayed innermost under CF-282's trap stack and kept wrapping Tab through the
// controls *behind* this dialog. The caption box, the three tiers, Cancel and
// Post were in nobody's Tab cycle — visible, and unreachable from a keyboard.
//
// A real renderer, unlike the sibling suite. `useFocusTrap.test.ts` pins the
// hook's own functions and says outright that no React renderer is involved;
// that is the right shape for the hook and cannot see this bug, which is a
// question of whether a *component* registers. React 19 exports `act` and
// `react-dom/client` gives us `createRoot`, so this needs no new dependency —
// the same reason test_posts_endpoints.py drives its handlers with a stub
// session rather than adding pytest-asyncio.
//
// **Every case here dispatches a real key at the document and lets the mounted
// listeners answer it.** An earlier draft of the Tab case called
// `trapTabWithin(card, …)` directly, which passes whether or not the composer
// registers anything — the implementation's own helper asked to confirm itself.
// Checked against the mutation: with the `useFocusTrap` call removed from
// PostComposerModal, all four fail.
import { useRef, useState, act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `@/lib/api` reaches Supabase at import time, which is not what is under test.
vi.mock("@/lib/api", () => ({ createPost: vi.fn() }));

import { PostComposerModal } from "@/components/PostComposerModal";
import { useFocusTrap, FOCUSABLE } from "@/lib/useFocusTrap";
import type { Clip } from "@/lib/api";

const CLIP = {
  id: "clip-1",
  game_id: "game-1",
  action_type: "spike",
  confidence: 0.9,
  start_time: 10,
  end_time: 18,
  clip_url: "https://example.test/c.mp4",
  thumbnail_url: null,
  labels: [],
  effective_visibility: "public",
} as unknown as Clip;

/**
 * ClipModal, reduced to the part that matters: a card that traps Tab, with the
 * composer mounted over it. Without this the test proves nothing — the bug is
 * an interaction between two traps, so one of them has to be real.
 */
function OuterOverlay({ onClose }: { onClose: () => void }) {
  const cardRef = useRef<HTMLDivElement>(null);
  // Starts closed and is opened by a click, which is the app's sequence and is
  // load-bearing here. Rendering both in one commit inverts the trap stack:
  // React runs child effects before parent effects, so the composer would push
  // its token first and ClipModal's would land on top of it. The app cannot
  // reach that — `composingFor` starts null and the composer mounts on a later
  // commit — but a harness that mounts them together tests the opposite of what
  // ships.
  const [composing, setComposing] = useState(false);
  useFocusTrap(cardRef, true, { initialFocus: () => cardRef.current });
  return (
    <>
      <div ref={cardRef} tabIndex={-1} id="outer-card">
        <button id="outer-download">Download</button>
        <button id="outer-post" onClick={() => setComposing(true)}>Post</button>
      </div>
      {composing && (
        <PostComposerModal
          clip={CLIP}
          onClose={() => { setComposing(false); onClose(); }}
        />
      )}
    </>
  );
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
});

function press(key: string, init: KeyboardEventInit = {}) {
  act(() => {
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init }),
    );
  });
}

function mount(onClose = () => {}) {
  act(() => { root.render(<OuterOverlay onClose={onClose} />); });
  const opener = document.getElementById("outer-post") as HTMLButtonElement;
  // focus() then click(): jsdom's click does not move focus, and neither does
  // WebKit's — which is the case `restoreFocusRef` exists for and which neither
  // overlay here passes. Focusing first models the browsers that do, so the
  // restore assertion below is about the trap rather than about jsdom. On
  // WebKit the composer closes to <body> instead of to this button; a small
  // real gap, and the hook already names the remedy.
  act(() => { opener.focus(); opener.click(); });
  const card = document.querySelector<HTMLElement>('[role="dialog"]');
  if (!card) throw new Error("composer card did not render");
  return card;
}

describe("PostComposerModal as a nested overlay", () => {
  it("moves focus into itself, onto the caption box", () => {
    mount();
    // The caption box specifically, not the first focusable — which is the X,
    // where the first Space after opening would discard the dialog.
    expect(document.activeElement).toBe(document.querySelector("textarea"));
  });

  it("keeps a real Tab inside itself rather than in the overlay behind it", () => {
    const card = mount();
    const inside = [...card.querySelectorAll<HTMLElement>(FOCUSABLE)];
    expect(inside.length).toBeGreaterThan(3);

    // From the composer's last control, Tab wraps to the composer's first.
    // Dispatched at the document so whichever trap is innermost answers it:
    // with none registered here, the outer card's trap does, and focus lands
    // among ClipModal's controls behind the dialog.
    inside[inside.length - 1].focus();
    press("Tab");
    expect(document.activeElement).toBe(inside[0]);
    expect(card.contains(document.activeElement)).toBe(true);

    // And Tab from inside the composer never reaches the overlay underneath,
    // which is the half a wrap assertion alone would not catch.
    const outer = document.getElementById("outer-card")!;
    for (let i = 0; i < inside.length + 2; i++) press("Tab");
    expect(outer.contains(document.activeElement)).toBe(false);
  });

  it("takes Escape as the innermost trap, closing itself and not the modal", () => {
    const onClose = vi.fn();
    mount(onClose);
    press("Escape");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("restores focus to the control that opened it", () => {
    mount();
    const opener = document.getElementById("outer-post") as HTMLButtonElement;
    // jsdom has no layout, so getClientRects() is empty for everything and
    // restoreFocusTo declines every target. Same stub the useFocusTrap suite
    // uses, and the reason that helper picked a plain method over
    // `checkVisibility()`: this branch is only reachable in a test if it can
    // be faked.
    opener.getClientRects = () => [{}] as unknown as DOMRectList;

    // Asserted before the restore, not just after: with no trap the focus never
    // leaves the Post button, so "it is on the opener at the end" is true for
    // the bug as well as the fix. This is the precondition that makes the last
    // line mean something.
    expect(document.activeElement).toBe(document.querySelector("textarea"));

    press("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});
