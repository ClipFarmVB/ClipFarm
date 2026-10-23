// @vitest-environment jsdom
//
// CF-272 (#306): Label, Trim and the player tag moved from ClipCard — whose
// one-line action row clipped Trim and the tag off its right edge — into an
// edit bar in ClipModal. These pin what the move has to keep and what it adds:
//
// - the CF-304 behaviours that used to be pinned on the card (a failed write is
//   said, the tag select locks while its write is out, jersey-less names);
// - edits reach the page as the fields that changed, which the page merges by
//   id onto its row as it is when the write lands — not the PATCH echo, whose
//   freshly presigned `clip_url` would restart the video, and not a copy of
//   the clip from before. A trim's echo is no exception: the api answers it
//   before the background re-cut runs, so its URL still serves the old cut,
//   and the media is swapped only once the new cut is there;
// - everything is keyed to the clip, so ← → closes a panel and a write that
//   settles late lands on its own clip, not the one now showing;
// - ← → leave the tag select alone, a key only picks a player while Enter
//   tags, and focus comes back to Tag after tagging.
import { act, useEffect, useState, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tagClip = vi.hoisted(() => vi.fn());
const updateClipLabels = vi.hoisted(() => vi.fn());
const trimClip = vi.hoisted(() => vi.fn());
const getClipShareUrl = vi.hoisted(() => vi.fn());
const probeDuration = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  tagClip,
  updateClipLabels,
  trimClip,
  getClipDownloadUrl: vi.fn(),
  getClipShareUrl,
  setClipVisibility: vi.fn(),
}));
// jsdom loads no media, so the re-cut's length is whatever a test says.
vi.mock("@/lib/mediaDuration", () => ({ probeDuration }));
vi.mock("@/lib/useMe", () => ({
  useMe: () => null,
  needsHandle: () => false,
}));
vi.mock("@/lib/download", () => ({ startCrossOriginDownload: vi.fn() }));
vi.mock("@/components/PostComposerModal", () => ({ PostComposerModal: () => null }));

import { ClipModal, mergeClipFields } from "@/components/ClipModal";
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

/** A keydown on the element itself, as a key pressed while it has focus. */
function keyOn(el: Element, key: string) {
  act(() => {
    el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  });
}

/** A key that moves a closed select: the browser fires its `change` inside
 *  the keydown's default action, in the same task. */
async function keyPick(s: HTMLSelectElement, key: string, value: string) {
  await act(async () => {
    s.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    s.value = value;
    s.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

/** Let the task the last event ran in end, and its timers with it. */
async function nextTask() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function chip(label: string): HTMLButtonElement {
  return button(label)!;
}

function busyChip(label: string): boolean {
  return chip(label).getAttribute("aria-disabled") === "true";
}

/** What the game page's `clips[]` held at its last render. */
let gridRows: Clip[] = [];

/**
 * The game page reduced to what the modal touches: a `clips[]` it owns, an
 * index for ← →, and `onUpdate` merged by id the way the page merges it.
 */
function Grid({ initial }: { initial: Clip[] }) {
  const [clips, setClips] = useState(initial);
  const [i, setI] = useState(0);
  useEffect(() => {
    gridRows = clips;
  });
  return (
    <ClipModal
      clip={clips[i]}
      onClose={() => {}}
      players={players}
      onPrev={i > 0 ? () => setI(i - 1) : undefined}
      onNext={i < clips.length - 1 ? () => setI(i + 1) : undefined}
      onUpdate={(id, fields) => setClips((prev) => mergeClipFields(prev, id, fields))}
    />
  );
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

  it("hands the parent the new player, not the echo", async () => {
    // The echo's re-signed `clip_url` would restart the video; only the tag's
    // own fields go to the page, which merges them onto its row.
    tagClip.mockResolvedValue(echo({ player_id: "p2", player_name: "Bob" }));
    const onUpdate = vi.fn();
    await mount({ clip: makeClip({ labels: ["dig"] }), onUpdate });

    await click(button("Tag player"));
    await choose(select()!, "p2");

    expect(tagClip).toHaveBeenCalledWith("clip-1", "p2");
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onUpdate).toHaveBeenCalledWith("clip-1", { player_id: "p2", player_name: "Bob" });
  });

  it("closes the select and puts focus back on the tag control", async () => {
    // Without the hand-back the select unmounts under focus and it drops to
    // <body>, so the next Tab starts from the top of the page.
    tagClip.mockResolvedValue(echo({ player_id: "p2", player_name: "Bob" }));
    await act(async () => root.render(<Grid initial={[makeClip()]} />));

    await click(button("Tag player"));
    expect(document.activeElement).toBe(select());
    await choose(select()!, "p2");

    expect(select()).toBeNull();
    expect(document.activeElement).toBe(button("Bob"));
  });

  it("does not pull focus to the Tag control of the clip the user moved to", async () => {
    // The hand-back is for the clip whose write settled. Once → has moved on,
    // focus is wherever the user put it on the next clip, and stays there.
    let resolve!: (c: Clip) => void;
    tagClip.mockReturnValue(new Promise<Clip>((r) => { resolve = r; }));
    await act(async () =>
      root.render(<Grid initial={[makeClip(), makeClip({ id: "clip-2", action_type: "serve" })]} />),
    );

    await click(button("Tag player"));
    await choose(select()!, "p2");
    await click(document.querySelector('[aria-label="Next clip"]')!);
    const label = button("Label")!;
    act(() => label.focus());

    await act(async () => resolve(echo({ player_id: "p2", player_name: "Bob" })));
    expect(document.activeElement).toBe(label);
  });

  it("only picks on an arrow key; Enter tags with the pick", async () => {
    // On a closed <select> ↓ fires `change`. Tagging on it put the first player
    // on the roster on the clip the moment a keyboard user started browsing.
    tagClip.mockResolvedValue(echo({ player_id: "p1", player_name: "Alice" }));
    const onUpdate = vi.fn();
    await mount({ onUpdate });

    await click(button("Tag player"));
    const s = select()!;
    await keyPick(s, "ArrowDown", "p1");
    expect(tagClip).not.toHaveBeenCalled();
    expect(document.getElementById("clip-edit-tag-hint")?.textContent).toBe("Enter to tag");
    expect(s.getAttribute("aria-describedby")).toBe("clip-edit-tag-hint");

    await act(async () => {
      s.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    });
    expect(tagClip).toHaveBeenCalledTimes(1);
    expect(tagClip).toHaveBeenCalledWith("clip-1", "p1");
    expect(onUpdate).toHaveBeenCalledWith("clip-1", { player_id: "p1", player_name: "Alice" });
  });

  it("drops a keyed pick when focus leaves the select without Enter", async () => {
    await mount();
    await click(button("Tag player"));
    const s = select()!;
    await keyPick(s, "ArrowDown", "p1");

    await act(async () => {
      s.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    });
    expect(select()).toBeNull();
    expect(tagClip).not.toHaveBeenCalled();
  });

  it("tags at once on a pick from a list a key opened, with no mousedown", async () => {
    // macOS: ↓ on a closed select opens the native menu instead of moving the
    // value, and a click in that menu sends the select no mousedown. The pick
    // arrives as a `change` in a later task, and must count as a pick from the
    // list, not as the key — or the next blur drops it. Windows has the same
    // shape for ↓ and then Alt+↓.
    tagClip.mockResolvedValue(echo({ player_id: "p2", player_name: "Bob" }));
    await mount();
    await click(button("Tag player"));
    const s = select()!;
    keyOn(s, "ArrowDown");
    await nextTask();
    await choose(s, "p2");
    expect(tagClip).toHaveBeenCalledWith("clip-1", "p2");
  });

  it("does not take focus back from a control the user moved to on the same clip", async () => {
    // R3-N1: a mouse pick, then the user clicks Label while the write is out.
    let resolve!: (c: Clip) => void;
    tagClip.mockReturnValue(new Promise<Clip>((r) => { resolve = r; }));
    await act(async () => root.render(<Grid initial={[makeClip()]} />));

    await click(button("Tag player"));
    await choose(select()!, "p2");
    const label = button("Label")!;
    act(() => label.focus());

    await act(async () => resolve(echo({ player_id: "p2", player_name: "Bob" })));
    expect(select()).toBeNull();
    expect(document.activeElement).toBe(label);
  });

  it("tags at once on a pick from the open list, even after arrow keys", async () => {
    // The pointer path: a mousedown opens the list, so the pick that follows
    // is a deliberate one, whatever keys came before it.
    tagClip.mockResolvedValue(echo({ player_id: "p2", player_name: "Bob" }));
    await mount();
    await click(button("Tag player"));
    const s = select()!;
    keyOn(s, "ArrowDown");
    await act(async () => {
      s.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    await choose(s, "p2");
    expect(tagClip).toHaveBeenCalledWith("clip-1", "p2");
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
    // "unknown" has no chip, so what the panel shows cannot tell a seeded
    // "unknown" from none; the write after a toggle can.
    updateClipLabels.mockResolvedValue(echo({ labels: ["dig"], action_type: "dig" }));
    await mount({ clip: makeClip({ action_type: "unknown", confidence: 0.4 }) });
    await click(button("Label"));
    for (const l of ["spike", "serve", "dig", "set", "block", "Not an action"]) {
      expect(pressed(l)).toBe(false);
    }

    await click(button("dig"));
    expect(updateClipLabels).toHaveBeenCalledWith("clip-1", ["dig"]);
  });

  it("adds to the seeded label, and hands the parent only the label fields", async () => {
    updateClipLabels.mockResolvedValue(
      echo({ labels: ["spike", "dig"], action_type: "spike", confidence: 0.93, player_name: null }),
    );
    const onUpdate = vi.fn();
    await mount({ clip: makeClip({ player_name: "Bob" }), onUpdate });

    await click(button("Label"));
    await click(button("dig"));

    expect(updateClipLabels).toHaveBeenCalledWith("clip-1", ["spike", "dig"]);
    expect(onUpdate).toHaveBeenCalledWith("clip-1", {
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
    expect(busyChip("dig")).toBe(true);

    await act(async () => reject(new Error("Clip is no longer available")));
    expect(alert).toHaveBeenCalledWith("Clip is no longer available");
    expect(pressed("dig")).toBe(false);
    expect(pressed("spike")).toBe(true);
  });

  it("keeps the pressed chip focusable while its write is out, and refuses a second press", async () => {
    // R3-M2: a `disabled` chip is blurred by the browser as it disables, so
    // focus fell to <body> and the next Tab started at the header. jsdom does
    // no focus fixup, so what is pinned is that the chip is never `disabled`
    // and that the busy check, not the attribute, refuses the second press.
    let release!: (c: Clip) => void;
    updateClipLabels.mockReturnValue(new Promise<Clip>((r) => { release = r; }));
    await mount();
    await click(button("Label"));
    const dig = chip("dig");
    act(() => dig.focus());

    await click(dig);
    expect(dig.disabled).toBe(false);
    expect(busyChip("dig")).toBe(true);
    expect(document.activeElement).toBe(dig);
    await click(chip("set"));
    expect(updateClipLabels).toHaveBeenCalledTimes(1);

    await act(async () =>
      release(echo({ labels: ["spike", "dig"], action_type: "spike", confidence: 0.93 })),
    );
    expect(busyChip("dig")).toBe(false);
    expect(document.activeElement).toBe(dig);
  });

  it("does not undo a tag that landed while the label write was out", async () => {
    // Two writes in flight on one clip. Each settles onto the row as the page
    // last rendered it, so the second does not write back the first's field.
    let releaseLabels!: (c: Clip) => void;
    updateClipLabels.mockReturnValue(new Promise<Clip>((r) => { releaseLabels = r; }));
    tagClip.mockResolvedValue(echo({ player_id: "p2", player_name: "Bob" }));
    await act(async () => root.render(<Grid initial={[makeClip()]} />));

    await click(button("Label"));
    await click(button("dig"));
    await click(button("Tag player"));
    await choose(select()!, "p2");
    expect(gridRows[0].player_name).toBe("Bob");

    await act(async () =>
      releaseLabels(echo({ labels: ["spike", "dig"], action_type: "spike", confidence: 0.93, player_name: null })),
    );
    expect(gridRows[0].labels).toEqual(["spike", "dig"]);
    expect(gridRows[0].player_name).toBe("Bob");
    expect(gridRows[0].clip_url).toBe("https://example.test/c.mp4");
  });

  it("does not undo a label when a trim settles after the user moved on", async () => {
    // A re-cut is slow. Label the clip while it runs, press →, and the trim
    // lands with the dialog on the next clip: it must merge onto the page's
    // row for its own clip as that row is now, with the label, not onto the
    // copy it started from — or the card loses the badge and the next toggle
    // on this clip writes a label set without it back to the server.
    let releaseTrim!: (c: Clip) => void;
    trimClip.mockReturnValue(new Promise<Clip>((r) => { releaseTrim = r; }));
    updateClipLabels.mockResolvedValue(
      echo({ labels: ["spike", "dig"], action_type: "spike", confidence: 0.93 }),
    );
    await act(async () =>
      root.render(<Grid initial={[makeClip(), makeClip({ id: "clip-2", action_type: "serve" })]} />),
    );

    await click(button("Trim"));
    const end = document.querySelector('[aria-label="End of clip"]')!;
    await click([...end.querySelectorAll("button")].find((b) => b.textContent === "+2s"));
    await click(button("Label"));
    await click(button("dig"));
    expect(gridRows[0].labels).toEqual(["spike", "dig"]);

    await click(document.querySelector('[aria-label="Next clip"]')!);
    await act(async () => releaseTrim(echo({ start_time: 10, end_time: 20 })));
    expect(gridRows[0].labels).toEqual(["spike", "dig"]);
    expect(gridRows[0].end_time).toBe(20);
    expect(gridRows[1].end_time).toBe(18);

    await click(document.querySelector('[aria-label="Previous clip"]')!);
    // Not pinned here whether the panel opened on this clip is still open.
    if (!document.getElementById("clip-edit-label")) await click(button("Label"));
    expect(pressed("dig")).toBe(true);
    await click(button("set"));
    expect(updateClipLabels).toHaveBeenLastCalledWith("clip-1", ["dig", "set"]);
  });
});

describe("trimming", () => {
  function step(edge: "Start" | "End", label: "−2s" | "+2s") {
    const group = document.querySelector(`[aria-label="${edge} of clip"]`)!;
    return [...group.querySelectorAll("button")].find((b) => b.textContent === label);
  }

  function video(): HTMLVideoElement {
    return document.querySelector("video")!;
  }

  function status(): string {
    return document.querySelector("#clip-edit-trim [role=status]")?.textContent ?? "";
  }

  it("takes the new bounds and keeps playing the cut it has", async () => {
    // R3-C1: the api answers a trim before its re-cut runs, and the re-cut
    // overwrites the same object, so the echo's re-signed URL serves the OLD
    // cut. Taking it reloaded the player onto that, at 0:00 and paused.
    trimClip.mockResolvedValue(echo({ start_time: 8, end_time: 18, player_name: null }));
    getClipShareUrl.mockReturnValue(new Promise(() => {}));
    const onUpdate = vi.fn();
    await mount({ clip: makeClip({ player_name: "Bob", labels: ["dig"] }), onUpdate });

    await click(button("Trim"));
    await click(step("Start", "−2s"));

    expect(trimClip).toHaveBeenCalledWith("clip-1", -2, 0);
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onUpdate).toHaveBeenCalledWith("clip-1", { start_time: 8, end_time: 18 });
    expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
    expect(status()).toContain("the player shows the old cut");
  });

  it("swaps in the re-cut once it has landed, and plays it", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    try {
      trimClip.mockResolvedValue(echo({ start_time: 10, end_time: 20, thumbnail_url: "https://example.test/t2.jpg" }));
      getClipShareUrl
        .mockResolvedValueOnce({ url: "https://example.test/poll-1.mp4" })
        .mockResolvedValueOnce({ url: "https://example.test/poll-2.mp4" });
      // The old 8s cut on the first poll, the new 10s one on the second.
      probeDuration.mockResolvedValueOnce(8.02).mockResolvedValueOnce(10.01);
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      expect(gridRows[0].end_time).toBe(20);
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");

      await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
      expect(probeDuration).toHaveBeenCalledWith("https://example.test/poll-1.mp4");
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");

      const played = vi.mocked(HTMLMediaElement.prototype.play).mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
      expect(video().getAttribute("src")).toBe("https://example.test/poll-2.mp4");
      expect(gridRows[0].thumbnail_url).toBe("https://example.test/t2.jpg");
      expect(vi.mocked(HTMLMediaElement.prototype.play).mock.calls.length).toBeGreaterThan(played);
      expect(status()).toBe("");
    } finally {
      vi.useRealTimers();
    }
  });

  it("says the new cut is not ready rather than guessing, once it stops looking", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    try {
      trimClip.mockResolvedValue(echo({ start_time: 10, end_time: 20 }));
      getClipShareUrl.mockResolvedValue({ url: "https://example.test/poll.mp4" });
      probeDuration.mockResolvedValue(8);
      const onUpdate = vi.fn();
      await mount({ onUpdate });

      await click(button("Trim"));
      await click(step("End", "+2s"));
      await act(async () => { await vi.advanceTimersByTimeAsync(6 * 60_000); });

      expect(onUpdate).toHaveBeenCalledTimes(1); // the bounds, never media
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      expect(status()).toContain("Reload to see it");
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps focus on the step just pressed while the trim is out", async () => {
    // R3-M2, for the steppers: see the label chip's test.
    let release!: (c: Clip) => void;
    trimClip.mockReturnValue(new Promise<Clip>((r) => { release = r; }));
    getClipShareUrl.mockReturnValue(new Promise(() => {}));
    await mount();
    await click(button("Trim"));
    const plus = step("End", "+2s")!;
    act(() => plus.focus());

    await click(plus);
    expect(plus.disabled).toBe(false);
    expect(plus.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(plus);
    await click(plus);
    expect(trimClip).toHaveBeenCalledTimes(1);

    await act(async () => release(echo({ start_time: 10, end_time: 20 })));
    expect(document.activeElement).toBe(plus);
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
    expect(busyChip("dig")).toBe(true); // the premise: clip-1's write is out

    const second = makeClip({ id: "clip-2", action_type: "serve" });
    await mount({ clip: second, onUpdate });
    await click(button("Label"));
    expect(busyChip("dig")).toBe(false);
    expect(pressed("dig")).toBe(false);
    expect(pressed("serve")).toBe(true);

    await act(async () =>
      release(echo({ labels: ["spike", "dig"], action_type: "spike", confidence: 0.93 })),
    );
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onUpdate.mock.calls[0][0]).toBe("clip-1");
    expect(pressed("serve")).toBe(true);
  });
});

describe("the keyboard", () => {
  it("leaves ← → to the tag select while it has focus", async () => {
    // On a closed <select> the arrows change the option, which picks a player
    // for Enter to tag; navigating as well would pick one and move off the
    // clip.
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
