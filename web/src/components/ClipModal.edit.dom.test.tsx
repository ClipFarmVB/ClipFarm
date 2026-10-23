// @vitest-environment jsdom
//
// CF-272 (#306): Label, Trim and the player tag moved from ClipCard — whose
// one-line action row clipped Trim and the tag off its right edge — into an
// edit bar in ClipModal. These pin what the move has to keep and what it adds:
//
// - the CF-304 behaviours that used to be pinned on the card (a failed write is
//   said, the tag select locks while its write is out, jersey-less names);
// - edits reach the page's row as the fields that changed, merged onto that
//   row, not the PATCH echo — whose freshly presigned `clip_url` would restart
//   the video — except a trim, which really changes the media;
// - everything is keyed to the clip, so ← → closes a panel and a write that
//   settles late lands on its own clip, not the one now showing;
// - ← → leave the tag select alone, and focus comes back to Tag after tagging.
import { act, useState, type ComponentProps } from "react";
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
  getClipShareUrl: vi.fn(),
  setClipVisibility: vi.fn(),
}));
vi.mock("@/lib/useMe", () => ({
  useMe: () => null,
  needsHandle: () => false,
}));
vi.mock("@/lib/download", () => ({ startCrossOriginDownload: vi.fn() }));
vi.mock("@/components/PostComposerModal", () => ({ PostComposerModal: () => null }));

import { ClipModal } from "@/components/ClipModal";
import type { Clip, Player } from "@/lib/api";

function makeClip(over: Partial<Clip> = {}): Clip {
  return {
    id: "clip-1",
    game_id: "game-1",
    player_id: null,
    player_name: null,
    action_type: "spike",
    confidence: 0.9,
    highlight_score: null,
    start_time: 10,
    end_time: 18,
    clip_url: "https://example.test/c.mp4",
    thumbnail_url: "https://example.test/c.jpg",
    labels: [],
    created_at: "2026-01-01T00:00:00Z",
    source_available: true,
    effective_visibility: "private",
    ...over,
  } as Clip;
}

/** A PATCH echo shaped like the api's: a re-signed URL on every response. */
function echo(over: Partial<Clip>): Clip {
  return makeClip({ clip_url: "https://example.test/re-signed.mp4", ...over });
}

const players: Player[] = [
  { id: "p1", name: "Alice", jersey_number: null, team_id: null, photo_url: null },
  { id: "p2", name: "Bob", jersey_number: 7, team_id: null, photo_url: null },
];

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  HTMLMediaElement.prototype.load = vi.fn();
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

async function mount(props: Partial<ComponentProps<typeof ClipModal>> = {}) {
  await act(async () => {
    root.render(<ClipModal clip={makeClip()} onClose={() => {}} players={players} {...props} />);
  });
}

function button(text: string | RegExp): HTMLButtonElement | undefined {
  return [...document.querySelectorAll("button")].find((b) => {
    const t = (b.textContent ?? "").trim();
    return typeof text === "string" ? t === text : text.test(t);
  });
}

async function click(el: Element | undefined) {
  if (!el) throw new Error("nothing to click");
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

async function choose(select: HTMLSelectElement, value: string) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function press(key: string) {
  act(() => {
    (document.activeElement ?? document.body).dispatchEvent(
      new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }),
    );
  });
}

function select(): HTMLSelectElement | null {
  return document.querySelector("select");
}

function pressed(label: string): boolean {
  return button(label)?.getAttribute("aria-pressed") === "true";
}

describe("the edit bar", () => {
  it("offers Label, Trim and the player tag when the page passes a roster", async () => {
    await mount();
    const bar = document.querySelector('[role="group"][aria-label="Edit clip"]');
    expect(bar).not.toBeNull();
    const labels = [...bar!.querySelectorAll("button")].map((b) => b.textContent?.trim());
    expect(labels).toEqual(["Label", "Trim", "Tag player"]);
  });

  it("is not there when the page passes no roster", async () => {
    await act(async () => {
      root.render(<ClipModal clip={makeClip()} onClose={() => {}} />);
    });
    expect(document.querySelector('[aria-label="Edit clip"]')).toBeNull();
    expect(button("Label")).toBeUndefined();
  });

  it("shows the tagged player's name on the tag control", async () => {
    await mount({ clip: makeClip({ player_name: "Bob" }) });
    expect(button("Bob")).toBeDefined();
  });

  it("disables Trim once the source has expired, and says why", async () => {
    await mount({ clip: makeClip({ source_available: false }) });
    const trim = button("Trim")!;
    expect(trim.disabled).toBe(true);
    expect(trim.title).toContain("Source video expired");
  });
});

describe("tagging a player", () => {
  it("lists the roster, with no stray hash for a player without a number", async () => {
    // `jersey_number` is nullable and the api orders it `nullslast`, so this
    // is a shipped row shape. React renders `null` as nothing, so the bug was
    // "# Alice", not "#null Alice" — asserting the exact text is what fails.
    await mount();
    await click(button("Tag player"));
    const options = [...select()!.querySelectorAll("option")].map((o) => o.textContent);
    expect(options).toContain("Alice");
    expect(options).toContain("#7 Bob");
  });

  it("hands the parent its own row with the new player, not the echo", async () => {
    // The echo's re-signed `clip_url` would restart the video; the row keeps
    // its labels and URL and takes the tag.
    const shown = makeClip({ labels: ["dig"] });
    tagClip.mockResolvedValue(echo({ player_id: "p2", player_name: "Bob" }));
    const onUpdate = vi.fn();
    await mount({ clip: shown, onUpdate });

    await click(button("Tag player"));
    await choose(select()!, "p2");

    expect(tagClip).toHaveBeenCalledWith("clip-1", "p2");
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onUpdate.mock.calls[0][0]).toEqual({ ...shown, player_id: "p2", player_name: "Bob" });
  });

  it("closes the select and puts focus back on the tag control", async () => {
    // Without the hand-back the select unmounts under focus and it drops to
    // <body>, so the next Tab starts from the top of the page.
    tagClip.mockResolvedValue(echo({ player_id: "p2", player_name: "Bob" }));
    function Page() {
      const [clip, setClip] = useState(makeClip());
      return <ClipModal clip={clip} onClose={() => {}} players={players} onUpdate={setClip} />;
    }
    await act(async () => root.render(<Page />));

    await click(button("Tag player"));
    expect(document.activeElement).toBe(select());
    await choose(select()!, "p2");

    expect(select()).toBeNull();
    expect(document.activeElement).toBe(button("Bob"));
  });

  it("locks the select while the write is in flight, and a blur cannot close it", async () => {
    // `focusout`, not `blur`: React's onBlur listens for the bubbling one. See
    // the card's old comment on this control for why the guard exists.
    let resolve!: (c: Clip) => void;
    tagClip.mockReturnValue(new Promise<Clip>((r) => { resolve = r; }));
    await mount();

    await click(button("Tag player"));
    const s = select()!;
    await choose(s, "p2");
    expect(s.disabled).toBe(true);

    await act(async () => {
      s.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    });
    expect(select()).not.toBeNull();

    await act(async () => resolve(echo({ player_id: "p2", player_name: "Bob" })));
    expect(select()).toBeNull();
  });

  it("says a tag failed, and tells the parent nothing", async () => {
    tagClip.mockRejectedValue(new Error("Clip is no longer available"));
    const alert = vi.fn();
    vi.stubGlobal("alert", alert);
    const onUpdate = vi.fn();
    await mount({ onUpdate });

    await click(button("Tag player"));
    await choose(select()!, "p1");

    expect(alert).toHaveBeenCalledWith("Clip is no longer available");
    expect(onUpdate).not.toHaveBeenCalled();
  });
});

describe("labelling", () => {
  it("opens on the model's call when nobody has labelled the clip", async () => {
    // `labels: []` with `action_type: "spike"` is the shape of every clip
    // straight out of the pipeline. The card seeded the panel this way.
    await mount();
    await click(button("Label"));
    expect(pressed("spike")).toBe(true);
    expect(pressed("dig")).toBe(false);
  });

  it("seeds nothing from an unknown call", async () => {
    await mount({ clip: makeClip({ action_type: "unknown", confidence: 0.4 }) });
    await click(button("Label"));
    for (const l of ["spike", "serve", "dig", "set", "block", "Not an action"]) {
      expect(pressed(l)).toBe(false);
    }
  });

  it("adds to the seeded label, and hands the parent its own row with the new labels", async () => {
    const shown = makeClip({ player_name: "Bob" });
    updateClipLabels.mockResolvedValue(
      echo({ labels: ["spike", "dig"], action_type: "spike", confidence: 0.93, player_name: null }),
    );
    const onUpdate = vi.fn();
    await mount({ clip: shown, onUpdate });

    await click(button("Label"));
    await click(button("dig"));

    expect(updateClipLabels).toHaveBeenCalledWith("clip-1", ["spike", "dig"]);
    expect(onUpdate.mock.calls[0][0]).toEqual({
      ...shown,
      labels: ["spike", "dig"],
      action_type: "spike",
      confidence: 0.93,
    });
  });

  it("shows the new label at once, and rolls it back with the reason when the write fails", async () => {
    let reject!: (e: Error) => void;
    updateClipLabels.mockReturnValue(new Promise<Clip>((_, r) => { reject = r; }));
    const alert = vi.fn();
    vi.stubGlobal("alert", alert);
    await mount();

    await click(button("Label"));
    await click(button("dig"));
    expect(pressed("dig")).toBe(true);
    expect(button("dig")!.disabled).toBe(true);

    await act(async () => reject(new Error("Clip is no longer available")));
    expect(alert).toHaveBeenCalledWith("Clip is no longer available");
    expect(pressed("dig")).toBe(false);
    expect(pressed("spike")).toBe(true);
  });

  it("does not undo a tag that landed while the label write was out", async () => {
    // Two writes in flight on one clip. Each settles onto the row as the page
    // last rendered it, so the second does not write back the first's field.
    let releaseLabels!: (c: Clip) => void;
    updateClipLabels.mockReturnValue(new Promise<Clip>((r) => { releaseLabels = r; }));
    tagClip.mockResolvedValue(echo({ player_id: "p2", player_name: "Bob" }));
    // What the page's row holds after each update, as the page would splice it.
    const rows = vi.fn();
    function Page() {
      const [clip, setClip] = useState(makeClip());
      return (
        <ClipModal
          clip={clip}
          onClose={() => {}}
          players={players}
          onUpdate={(c) => { rows(c); setClip(c); }}
        />
      );
    }
    const latest = () => rows.mock.lastCall![0] as Clip;
    await act(async () => root.render(<Page />));

    await click(button("Label"));
    await click(button("dig"));
    await click(button("Tag player"));
    await choose(select()!, "p2");
    expect(latest().player_name).toBe("Bob");

    await act(async () =>
      releaseLabels(echo({ labels: ["spike", "dig"], action_type: "spike", confidence: 0.93, player_name: null })),
    );
    expect(rows).toHaveBeenCalledTimes(2);
    expect(latest().labels).toEqual(["spike", "dig"]);
    expect(latest().player_name).toBe("Bob");
    expect(latest().clip_url).toBe("https://example.test/c.mp4");
  });
});

describe("trimming", () => {
  it("re-cuts, and takes the trim's new bounds and media", async () => {
    // The one edit that really changes the video, so its URL is taken.
    const shown = makeClip({ player_name: "Bob", labels: ["dig"] });
    trimClip.mockResolvedValue(echo({ start_time: 8, end_time: 18, player_name: null }));
    const onUpdate = vi.fn();
    await mount({ clip: shown, onUpdate });

    await click(button("Trim"));
    const start = document.querySelector('[aria-label="Start of clip"]')!;
    await click([...start.querySelectorAll("button")].find((b) => b.textContent === "−2s"));

    expect(trimClip).toHaveBeenCalledWith("clip-1", -2, 0);
    expect(onUpdate.mock.calls[0][0]).toEqual({
      ...shown,
      start_time: 8,
      end_time: 18,
      clip_url: "https://example.test/re-signed.mp4",
    });
  });

  it("says a trim failed rather than silently keeping the old bounds", async () => {
    trimClip.mockRejectedValue(new Error("Source video expired"));
    const alert = vi.fn();
    vi.stubGlobal("alert", alert);
    await mount();

    await click(button("Trim"));
    await click(button("+2s"));

    expect(trimClip).toHaveBeenCalled();
    expect(alert).toHaveBeenCalledWith("Source video expired");
  });
});

describe("moving to another clip", () => {
  it("closes an open panel", async () => {
    await mount();
    await click(button("Label"));
    expect(document.getElementById("clip-edit-label")).not.toBeNull();

    await mount({ clip: makeClip({ id: "clip-2" }) });
    expect(document.getElementById("clip-edit-label")).toBeNull();
  });

  it("does not show another clip's write as in flight, or let it land on this one", async () => {
    let release!: (c: Clip) => void;
    updateClipLabels.mockReturnValue(new Promise<Clip>((r) => { release = r; }));
    const onUpdate = vi.fn();
    await mount({ onUpdate });
    await click(button("Label"));
    await click(button("dig"));
    expect(button("dig")!.disabled).toBe(true); // the premise: clip-1's write is out

    const second = makeClip({ id: "clip-2", action_type: "serve" });
    await mount({ clip: second, onUpdate });
    await click(button("Label"));
    expect(button("dig")!.disabled).toBe(false);
    expect(pressed("dig")).toBe(false);
    expect(pressed("serve")).toBe(true);

    await act(async () =>
      release(echo({ labels: ["spike", "dig"], action_type: "spike", confidence: 0.93 })),
    );
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onUpdate.mock.calls[0][0].id).toBe("clip-1");
    expect(pressed("serve")).toBe(true);
  });
});

describe("the keyboard", () => {
  it("leaves ← → to the tag select while it has focus", async () => {
    // On a closed <select> the arrows change the option, which fires the tag
    // write; navigating as well would tag the clip and move off it.
    const onNext = vi.fn();
    const onPrev = vi.fn();
    await mount({ onNext, onPrev });

    press("ArrowRight"); // the control: from the dialog card they navigate
    expect(onNext).toHaveBeenCalledTimes(1);

    await click(button("Tag player"));
    expect(document.activeElement).toBe(select());
    press("ArrowRight");
    press("ArrowLeft");
    expect(onNext).toHaveBeenCalledTimes(1);
    expect(onPrev).not.toHaveBeenCalled();
  });

  it("still closes the dialog on Escape from the tag select", async () => {
    const onClose = vi.fn();
    await mount({ onClose });
    await click(button("Tag player"));
    expect(document.activeElement).toBe(select());

    press("Escape");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
