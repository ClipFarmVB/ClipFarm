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
// - everything is keyed to the clip, so ← → hides a panel and a write that
//   settles late lands on its own clip, not the one now showing;
// - ← → leave the tag select alone, a key only picks a player while Enter
//   tags, and focus comes back to Tag after tagging; ← → from a chip or step
//   hand focus to the panel's toggle, as the panel goes with the clip.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
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
  // Not only the calls: a test that fails part-way leaves its unused
  // `mockResolvedValueOnce` replies queued, and the next test would get them.
  for (const m of [tagClip, updateClipLabels, trimClip, getClipShareUrl, probeDuration]) m.mockReset();
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
 * Closing unmounts the modal, as both pages do, and "Open clip" mounts it
 * again on the same rows.
 */
function Grid({ initial, sourceDuration }: { initial: Clip[]; sourceDuration?: number }) {
  const [clips, setClips] = useState(initial);
  const [i, setI] = useState(0);
  const [open, setOpen] = useState(true);
  useEffect(() => {
    gridRows = clips;
  });
  if (!open) return <button onClick={() => setOpen(true)}>Open clip</button>;
  return (
    <ClipModal
      clip={clips[i]}
      onClose={() => setOpen(false)}
      players={players}
      sourceDuration={sourceDuration}
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

  it("does not close a select opened on the next clip when the earlier write settles", async () => {
    // R7-N3: the write clears the open select only if it is still its own
    // clip's. Clearing it outright would shut the select the user had just
    // opened on clip B as clip A's tag landed.
    let resolve!: (c: Clip) => void;
    tagClip.mockReturnValue(new Promise<Clip>((r) => { resolve = r; }));
    await act(async () => root.render(<Grid initial={[makeClip(), makeClip({ id: "clip-2" })]} />));

    await click(button("Tag player"));
    await choose(select()!, "p2");
    await click(document.querySelector('[aria-label="Next clip"]')!);
    await click(button("Tag player"));
    expect(document.activeElement).toBe(select());

    await act(async () => resolve(echo({ player_id: "p2", player_name: "Bob" })));
    expect(select()).not.toBeNull();
    expect(document.activeElement).toBe(select());
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
    // R9-N4: an instruction, so the readable token, not `subtle`, which
    // globals.css keeps for decorative text and sits under AA.
    expect(document.getElementById("clip-edit-tag-hint")?.className).toContain("text-muted");

    await act(async () => {
      s.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    });
    expect(tagClip).toHaveBeenCalledTimes(1);
    expect(tagClip).toHaveBeenCalledWith("clip-1", "p1");
    expect(onUpdate).toHaveBeenCalledWith("clip-1", { player_id: "p1", player_name: "Alice" });
  });

  it.each(["Home", "End", "PageUp", "PageDown", "b"])(
    "only picks on %s too",
    async (key) => {
      // The other keys that move a closed <select>'s value, type-ahead among
      // them.
      await mount();
      await click(button("Tag player"));
      await keyPick(select()!, key, "p2");
      expect(tagClip).not.toHaveBeenCalled();
      expect(document.getElementById("clip-edit-tag-hint")?.textContent).toBe("Enter to tag");
    },
  );

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

  it("paints the pressed Not an action chip with the danger token, set per theme", async () => {
    // R13-M2: the card's `text-red-400` on a `red-500` tint was 2.1:1 over
    // the light theme's surface. `danger` is #b91c1c there (4.76:1 on its own
    // 10% tint) and #f87171 in the dark theme (5.83:1); the figures are in
    // globals.css, which has to define the token for both themes.
    await mount({ clip: makeClip({ labels: ["not_an_action"] }) });
    await click(button("Label"));
    const notAction = chip("Not an action");
    expect(notAction.getAttribute("aria-pressed")).toBe("true");
    expect(notAction.className).toContain("text-danger");
    expect(notAction.className).toContain("bg-danger/10");
    expect(notAction.className).not.toMatch(/red-\d/);
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../app/globals.css"), "utf8");
    expect(css).toMatch(/--color-danger:\s+var\(--cf-danger\)/);
    expect(css.match(/--cf-danger:/g)).toHaveLength(2);
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

  it("says a reload shows the new cut, rather than guessing, once it stops looking", async () => {
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

  /** Run `body` on fake timers; `wait(ms)` lets the polls due in that time run. */
  async function withTimers(body: (wait: (ms: number) => Promise<void>) => Promise<void>) {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    try {
      await body(async (ms) => {
        await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
      });
    } finally {
      vi.useRealTimers();
    }
  }

  /** Each poll's share URL is `poll-N.mp4`, so a swap says which poll it took. */
  function numberedPolls() {
    let n = 0;
    getClipShareUrl.mockImplementation(async () => ({ url: `https://example.test/poll-${++n}.mp4` }));
  }

  it("swaps in a trim past the end of the source, which the re-cut cuts short", async () => {
    // R5-M1: the api clamps only the start, and ffmpeg's `-t` stops at the end
    // of the source. A clip 110–118 of a 120s game, End +2s twice: the bounds
    // say 110–122, and the re-cut is 10s long, not 12.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 110, end_time: 120 }))
        .mockResolvedValueOnce(echo({ start_time: 110, end_time: 122 }));
      numberedPolls();
      probeDuration.mockResolvedValueOnce(8).mockResolvedValueOnce(10);
      await act(async () =>
        root.render(<Grid initial={[makeClip({ start_time: 110, end_time: 118 })]} sourceDuration={120} />),
      );

      await click(button("Trim"));
      await click(step("End", "+2s"));
      await click(step("End", "+2s"));
      expect(gridRows[0].end_time).toBe(122);

      await wait(3_000);
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      await wait(5_000);
      expect(video().getAttribute("src")).toBe("https://example.test/poll-2.mp4");
      expect(status()).toBe("");
    });
  });

  it("does not guess at a trim cut short by the source's end where the page does not know its length", async () => {
    // No `sourceDuration` (the collections page): the 10s cut the re-cut
    // makes matches nothing the 11s bounds expect. Until R11-M1 a reading
    // shorter than the bounds was taken as that cut; see the reopen tests
    // below for what that inferred wrongly. Now the watch says a reload
    // shows it, and the player keeps what it has.
    await withTimers(async (wait) => {
      trimClip.mockResolvedValueOnce(echo({ start_time: 110, end_time: 121 }));
      numberedPolls();
      probeDuration.mockResolvedValueOnce(9).mockResolvedValue(10);
      await act(async () => root.render(<Grid initial={[makeClip({ start_time: 110, end_time: 119 })]} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));

      await wait(3_000 + 5_000);
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      expect(status()).toContain("the player shows the old cut");
      await wait(6 * 60_000);
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      expect(status()).toContain("Reload to see it");
    });
  });

  it("swaps in a shift, whose new cut is as long as the old one", async () => {
    // R5-N1: Start +2s, then End +2s before the first re-cut lands. The 8s
    // cut becomes 6s, then 8s again over different footage; the 6s step on
    // the way is what tells the second 8s reading from the first.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 12, end_time: 18 }))
        .mockResolvedValueOnce(echo({ start_time: 12, end_time: 20 }));
      numberedPolls();
      probeDuration.mockResolvedValueOnce(8).mockResolvedValueOnce(6).mockResolvedValueOnce(8);
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      await click(step("Start", "+2s"));
      await click(step("End", "+2s"));
      expect(status()).toContain("the player shows the old cut");

      await wait(3_000 + 5_000);
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      expect(status()).toContain("the player shows the old cut");
      await wait(5_000);
      expect(video().getAttribute("src")).toBe("https://example.test/poll-3.mp4");
      expect(status()).toBe("");
    });
  });

  it("says nothing for a trim the api clamped to the clip it already is", async () => {
    // R5-N1: Start −2s on a clip at 0:00 comes back with the same bounds.
    // There is no new cut to wait for, and "reload to see it" would show the
    // same clip.
    await withTimers(async (wait) => {
      trimClip.mockResolvedValueOnce(echo({ start_time: 0, end_time: 8 }));
      await mount({ clip: makeClip({ start_time: 0, end_time: 8 }) });

      await click(button("Trim"));
      await click(step("Start", "−2s"));
      expect(trimClip).toHaveBeenCalledWith("clip-1", -2, 0);
      expect(status()).toBe("");

      await wait(6 * 60_000);
      expect(getClipShareUrl).not.toHaveBeenCalled();
      expect(status()).toBe("");
    });
  });

  it("keeps watching through one step out and one back, and swaps in the cut that comes back", async () => {
    // R9-M1: End +2s, then End −2s before its re-cut lands. The bounds are
    // back where the player is, but the step out is still queued and will
    // overwrite the object first, so the object steps 8 → 10 → 8. Ending the
    // watch here (as before) left the next trim matched against those two
    // stale re-cuts as they landed.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 18 }));
      numberedPolls();
      probeDuration.mockResolvedValueOnce(8).mockResolvedValueOnce(10).mockResolvedValueOnce(8);
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      await click(step("End", "−2s"));
      expect(status()).toContain("the player shows the old cut");

      await wait(3_000 + 5_000); // 8, then 10: the step out landing
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      expect(status()).toContain("the player shows the old cut");
      await wait(5_000); // 8: the step back
      expect(video().getAttribute("src")).toBe("https://example.test/poll-3.mp4");
      expect(status()).toBe("");
    });
  });

  it("walks the next trim through the re-cuts still queued after a step out and back", async () => {
    // R9-M1 as reproduced on the game page: End +2s, End −2s, Start −2s on
    // 10–18 of a 120s source. The object steps 8 → 10 → 8 → 10, and the 10
    // read second is the FIRST trim's cut, over 10–20; the cut asked for is
    // the 10s one over 8–18, two re-cuts later. With the chain dropped on the
    // step back, the third trim started a chain of its own and that stale 10
    // confirmed it: the wrong footage was swapped in, with the status cleared
    // under bounds reading 8–18.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 18 }))
        .mockResolvedValueOnce(echo({ start_time: 8, end_time: 18 }));
      numberedPolls();
      probeDuration
        .mockResolvedValueOnce(8)
        .mockResolvedValueOnce(10)
        .mockResolvedValueOnce(8)
        .mockResolvedValueOnce(10);
      await act(async () => root.render(<Grid initial={[makeClip()]} sourceDuration={120} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      await click(step("End", "−2s"));
      await click(step("Start", "−2s"));
      expect(gridRows[0].start_time).toBe(8);
      expect(gridRows[0].end_time).toBe(18);

      await wait(3_000 + 5_000); // 8, then 10: the first trim's cut, over 10–20
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      expect(status()).toContain("the player shows the old cut");
      await wait(5_000); // 8: the step back
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      await wait(5_000); // 10: the cut over 8–18
      expect(video().getAttribute("src")).toBe("https://example.test/poll-4.mp4");
      expect(status()).toBe("");
    });
  });

  it("walks the next trim through them where the page does not know the source's length", async () => {
    // The same on the collections page: Start +2s, Start −2s, End +2s. The
    // object steps 8 → 6 → 8 → 10. With the chain dropped, the 6 — the first
    // trim's cut, over 12–18 — read as the third trim's step cut short by the
    // source's end, and was swapped in for a trim that asked for 10–20.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 12, end_time: 18 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 18 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }));
      numberedPolls();
      probeDuration
        .mockResolvedValueOnce(8)
        .mockResolvedValueOnce(6)
        .mockResolvedValueOnce(8)
        .mockResolvedValueOnce(10);
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      await click(step("Start", "+2s"));
      await click(step("Start", "−2s"));
      await click(step("End", "+2s"));

      await wait(3_000 + 5_000); // 8, then 6: the first trim's cut, over 12–18
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      expect(status()).toContain("the player shows the old cut");
      await wait(5_000 + 5_000); // 8, then 10: the cut over 10–20
      expect(video().getAttribute("src")).toBe("https://example.test/poll-4.mp4");
      expect(status()).toBe("");
    });
  });

  it("keeps the chain through a step out and back made twice", async () => {
    // End +2s, −2s, +2s, −2s: four re-cuts, 8 → 10 → 8 → 10 → 8, and the cut
    // to show is the last of them. Before the fix neither step back left a
    // watch running at all.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 18 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 18 }));
      numberedPolls();
      probeDuration
        .mockResolvedValueOnce(8)
        .mockResolvedValueOnce(10)
        .mockResolvedValueOnce(8)
        .mockResolvedValueOnce(10)
        .mockResolvedValueOnce(8);
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      for (const label of ["+2s", "−2s", "+2s", "−2s"] as const) await click(step("End", label));

      await wait(3_000 + 3 * 5_000); // 8, 10, 8, 10
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      expect(status()).toContain("the player shows the old cut");
      await wait(5_000); // 8: the last step back
      expect(video().getAttribute("src")).toBe("https://example.test/poll-5.mp4");
      expect(status()).toBe("");
    });
  });

  it("drops the steps a reading has passed, not only the one it matched", async () => {
    // R13-N1: End +2s twice on 10–18, with both re-cuts landing between two
    // polls, so the 10 is never read: 8, then 12. The 12 is the second step,
    // and the first is passed with it; dropping only the matched step left
    // the watch waiting on the 10 until it gave up.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 22 }));
      numberedPolls();
      probeDuration.mockResolvedValueOnce(8).mockResolvedValueOnce(12);
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      await click(step("End", "+2s"));

      await wait(3_000); // 8
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      await wait(5_000); // 12, past the unseen 10
      expect(video().getAttribute("src")).toBe("https://example.test/poll-2.mp4");
      expect(status()).toBe("");
    });
  });

  it("measures the next trim from the cut it swapped in", async () => {
    // Once a re-cut lands, the player holds it: End −2s afterwards goes back
    // to the 8s footage, and that is a new cut to wait for and swap in, not a
    // trim back to what the player has. Nothing has measured the first cut
    // (jsdom), so the first watch's first reading, 8, is its baseline
    // (R13-M1); the second starts from the 10 the swap read (R16-M1), and
    // its first reading, 10 again, confirms nothing.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 18 }));
      numberedPolls();
      probeDuration
        .mockResolvedValueOnce(8)
        .mockResolvedValueOnce(10)
        .mockResolvedValueOnce(10)
        .mockResolvedValueOnce(8);
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      await wait(3_000 + 5_000); // 8, then 10
      expect(video().getAttribute("src")).toBe("https://example.test/poll-2.mp4");

      await click(step("End", "−2s"));
      expect(status()).toContain("the player shows the old cut");
      await wait(3_000); // 10: the cut it holds
      expect(video().getAttribute("src")).toBe("https://example.test/poll-2.mp4");
      await wait(5_000); // 8
      expect(video().getAttribute("src")).toBe("https://example.test/poll-4.mp4");
      expect(status()).toBe("");
    });
  });

  it("watches a trim whose request was still out when the earlier re-cut was swapped in", async () => {
    // R16-M1: End +2s (10–20) is being polled; End +2s again (10–22) has its
    // request out when the poll that confirms the first swaps it in. The
    // swap used to drop the held cut, so the second trim's reply found none,
    // built one from its own bounds, read as a trim back to what the player
    // holds, and ended the watch: the first cut kept playing under the second
    // trim's bounds with no status and no poll. The swap leaves the cut it
    // confirmed as the held cut, so the later reply differs from it and is
    // watched to its own landing.
    await withTimers(async (wait) => {
      let release: (c: Clip) => void = () => {};
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockReturnValueOnce(new Promise<Clip>((r) => { release = r; }));
      numberedPolls();
      probeDuration
        .mockResolvedValueOnce(8) // the baseline: nothing measured it in jsdom
        .mockResolvedValueOnce(10) // the first re-cut has landed
        .mockResolvedValueOnce(10) // still that cut
        .mockResolvedValueOnce(12); // the second has landed
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      await wait(3_000); // 8
      await click(step("End", "+2s"));
      expect(trimClip).toHaveBeenCalledTimes(2);
      await wait(5_000); // 10: the first cut is swapped in while the request is out
      expect(video().getAttribute("src")).toBe("https://example.test/poll-2.mp4");

      await act(async () => { release(echo({ start_time: 10, end_time: 22 })); });
      expect(gridRows[0].end_time).toBe(22);
      expect(status()).toContain("the player shows the old cut");
      await wait(3_000); // 10: the cut it holds
      expect(video().getAttribute("src")).toBe("https://example.test/poll-2.mp4");
      await wait(5_000); // 12
      expect(video().getAttribute("src")).toBe("https://example.test/poll-4.mp4");
      expect(gridRows[0].clip_url).toBe("https://example.test/poll-4.mp4");
      expect(status()).toBe("");
    });
  });

  /** The length the player reports for the cut it holds. jsdom loads no media
   *  and reports NaN, which the dialog reads as "not measured"; a browser with
   *  the metadata loaded reports the cut's real length. */
  function heldLength(seconds: number) {
    return vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(seconds);
  }

  async function reopen() {
    press("Escape");
    expect(document.querySelector("[role=dialog]")).toBeNull();
    await click(button("Open clip"));
    await click(button("Trim"));
  }

  it("waits for the trim made after a close and reopen, with the held cut measured", async () => {
    // R11-M1 (a): End +2s (10–20), close, reopen, End +2s (10–22), on the
    // collections page. The remounted dialog starts from a clean chain, so
    // the first trim's 10s cut is a reading that matches nothing. With the
    // shorter-reading fallback it was taken as the 12s step cut short and
    // swapped in under bounds reading 10–22, with the status cleared.
    await withTimers(async (wait) => {
      const held = heldLength(8);
      try {
        trimClip
          .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
          .mockResolvedValueOnce(echo({ start_time: 10, end_time: 22 }));
        numberedPolls();
        probeDuration.mockResolvedValueOnce(8).mockResolvedValueOnce(10).mockResolvedValueOnce(12);
        await act(async () => root.render(<Grid initial={[makeClip()]} />));

        await click(button("Trim"));
        await click(step("End", "+2s"));
        await reopen();
        expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
        await click(step("End", "+2s"));
        expect(gridRows[0].end_time).toBe(22);

        await wait(3_000 + 5_000); // 8, then 10: the first trim's cut
        expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
        expect(status()).toContain("the player shows the old cut");
        await wait(5_000); // 12: the cut asked for
        expect(video().getAttribute("src")).toBe("https://example.test/poll-3.mp4");
        expect(status()).toBe("");
      } finally {
        held.mockRestore();
      }
    });
  });

  it("waits for the trim made after a close and reopen, with the held cut unmeasured", async () => {
    // R11-M1 (b): the same, with the player's metadata not loaded. The held
    // length was then the row's arithmetic, 10, and the first poll's 8 (the
    // unchanged object) matched nothing; the fallback swapped the OLD cut
    // back in on that poll, restarted at 0:00. Since R13-M1 nothing is
    // seeded: the 8 is the baseline, and 10 and 12 are matched from it.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 22 }));
      numberedPolls();
      probeDuration.mockResolvedValueOnce(8).mockResolvedValueOnce(10).mockResolvedValueOnce(12);
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      await reopen();
      await click(step("End", "+2s"));

      await wait(3_000); // 8: the unchanged old object
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      expect(status()).toContain("the player shows the old cut");
      await wait(5_000); // 10
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      await wait(5_000); // 12
      expect(video().getAttribute("src")).toBe("https://example.test/poll-3.mp4");
      expect(status()).toBe("");
    });
  });

  it("waits for the trim made after a close and reopen on the game page", async () => {
    // The same sequence with the source's length known, which was already
    // right: 8 and 10 match nothing, 12 swaps in.
    await withTimers(async (wait) => {
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 22 }));
      numberedPolls();
      probeDuration.mockResolvedValueOnce(8).mockResolvedValueOnce(10).mockResolvedValueOnce(12);
      await act(async () => root.render(<Grid initial={[makeClip()]} sourceDuration={120} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      await reopen();
      await click(step("End", "+2s"));

      await wait(3_000 + 5_000);
      expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
      await wait(5_000);
      expect(video().getAttribute("src")).toBe("https://example.test/poll-3.mp4");
      expect(status()).toBe("");
    });
  });

  /** R13-M1: End +2s (10–20), close, reopen, Start +2s (12–20) on 10–18. The
   *  cut asked for is 8s — as long as the old cut the object still holds
   *  while the first re-cut is queued — and the object steps 8 → 10 → 8. */
  async function reopenThenTrimToOldLength() {
    trimClip
      .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
      .mockResolvedValueOnce(echo({ start_time: 12, end_time: 20 }));
    numberedPolls();
    probeDuration.mockResolvedValueOnce(8).mockResolvedValueOnce(10).mockResolvedValueOnce(8);
    await click(button("Trim"));
    await click(step("End", "+2s"));
    await reopen();
    await click(step("Start", "+2s"));
    expect(gridRows[0].start_time).toBe(12);
    expect(gridRows[0].end_time).toBe(20);
  }

  async function expectSwapOnThirdPoll(wait: (ms: number) => Promise<void>) {
    await wait(3_000); // 8: the unchanged old object
    expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
    expect(gridRows[0].clip_url).toBe("https://example.test/c.mp4");
    expect(status()).toContain("the player shows the old cut");
    await wait(5_000); // 10: the first trim's cut
    expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
    expect(status()).toContain("the player shows the old cut");
    await wait(5_000); // 8: the cut over 12–20
    expect(video().getAttribute("src")).toBe("https://example.test/poll-3.mp4");
    expect(gridRows[0].clip_url).toBe("https://example.test/poll-3.mp4");
    expect(status()).toBe("");
  }

  it("does not take the unchanged old cut for a trim as long as it, made after a close and reopen", async () => {
    // R13-M1 on the collections page, with the player's metadata not loaded.
    // Seeded from the reopened row's bounds (10), the chain took the first
    // poll's 8 as the cut asked for: the old footage swapped in under bounds
    // reading 12–20, the status cleared and the row's URL replaced. With
    // nothing measured, the first reading is only the baseline.
    await withTimers(async (wait) => {
      await act(async () => root.render(<Grid initial={[makeClip()]} />));
      await reopenThenTrimToOldLength();
      await expectSwapOnThirdPoll(wait);
    });
  });

  it("does not take the unchanged old cut for that trim on the game page either", async () => {
    // The same with the source's length known, which does not help: the
    // bounds' 10 is still the cut the object will hold, not the one it does.
    await withTimers(async (wait) => {
      await act(async () => root.render(<Grid initial={[makeClip()]} sourceDuration={120} />));
      await reopenThenTrimToOldLength();
      await expectSwapOnThirdPoll(wait);
    });
  });

  it("waits through that trim's queued re-cut with the held cut measured", async () => {
    // The same with the metadata loaded (the browser's usual case): the
    // held cut is 8 as measured, so the first 8 is skipped as unchanged.
    await withTimers(async (wait) => {
      const held = heldLength(8);
      try {
        await act(async () => root.render(<Grid initial={[makeClip()]} />));
        await reopenThenTrimToOldLength();
        await expectSwapOnThirdPoll(wait);
      } finally {
        held.mockRestore();
      }
    });
  });

  it("takes the length the player reports once its metadata arrives before the first poll", async () => {
    // The held cut unmeasured as the step is pressed (the dialog has just
    // reopened), measured by the first poll. The first reading is then
    // matched as usual, so a re-cut landed within those 3s is swapped in on
    // it rather than taken as the baseline.
    await withTimers(async (wait) => {
      trimClip.mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }));
      numberedPolls();
      probeDuration.mockResolvedValueOnce(10);
      await act(async () => root.render(<Grid initial={[makeClip()]} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      const held = heldLength(8);
      try {
        await wait(3_000); // 10: the new cut, already landed
        expect(video().getAttribute("src")).toBe("https://example.test/poll-1.mp4");
        expect(status()).toBe("");
      } finally {
        held.mockRestore();
      }
    });
  });

  it("does not take a reading from the player once → has put another clip in it", async () => {
    // The same, with the user on the next clip by the first poll: the
    // player's length is that clip's, not the held cut's, so the reading is
    // the baseline and the cut is swapped in on the next poll that differs.
    await withTimers(async (wait) => {
      trimClip.mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }));
      numberedPolls();
      probeDuration.mockResolvedValueOnce(10).mockResolvedValueOnce(10);
      const other = makeClip({ id: "clip-2", start_time: 30, end_time: 42, clip_url: "https://example.test/d.mp4" });
      await act(async () => root.render(<Grid initial={[makeClip(), other]} />));

      await click(button("Trim"));
      await click(step("End", "+2s"));
      press("ArrowRight");
      expect(video().getAttribute("src")).toBe("https://example.test/d.mp4");
      const held = heldLength(12);
      try {
        await wait(3_000 + 5_000); // 10, 10: the first is the baseline
        expect(gridRows[0].clip_url).toBe("https://example.test/c.mp4");
      } finally {
        held.mockRestore();
      }
    });
  });

  it("expects a step back to the held cut to read as the player measured it", async () => {
    // R11-N2: a row 110–122 whose stored end is past the source, so the
    // player holds a 10s cut, on a page that does not know the source's
    // length. Start +2s, then Start −2s back: the object steps 10 → 8 → 10,
    // and the step back is the held cut's 10s, not the bounds' 12s. Expecting
    // 12 left the second 10 taken as the step out and the watch waiting on a
    // length the object never gets.
    await withTimers(async (wait) => {
      const held = heldLength(10);
      try {
        trimClip
          .mockResolvedValueOnce(echo({ start_time: 112, end_time: 122 }))
          .mockResolvedValueOnce(echo({ start_time: 110, end_time: 122 }));
        numberedPolls();
        probeDuration.mockResolvedValueOnce(10).mockResolvedValueOnce(8).mockResolvedValueOnce(10);
        await act(async () => root.render(<Grid initial={[makeClip({ start_time: 110, end_time: 122 })]} />));

        await click(button("Trim"));
        await click(step("Start", "+2s"));
        await click(step("Start", "−2s"));

        await wait(3_000 + 5_000); // 10, then 8: the step out
        expect(video().getAttribute("src")).toBe("https://example.test/c.mp4");
        await wait(5_000); // 10: the step back
        expect(video().getAttribute("src")).toBe("https://example.test/poll-3.mp4");
        expect(status()).toBe("");
      } finally {
        held.mockRestore();
      }
    });
  });

  it("stops polling when the dialog closes", async () => {
    await withTimers(async (wait) => {
      trimClip.mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }));
      getClipShareUrl.mockResolvedValue({ url: "https://example.test/poll.mp4" });
      probeDuration.mockResolvedValue(8);
      await mount();
      await click(button("Trim"));
      await click(step("End", "+2s"));

      await act(async () => root.render(<div />));
      await wait(60_000);
      expect(getClipShareUrl).not.toHaveBeenCalled();
    });
  });

  it("starts no poll for a trim that settles after the dialog closed", async () => {
    // R7-M1: the unmount cleanup stops the watches registered so far. A trim
    // still out then registered its own as it settled, into a map already
    // cleared, and that poll ran for up to five minutes and swapped media
    // onto a page nobody was looking at.
    await withTimers(async (wait) => {
      let release!: (c: Clip) => void;
      trimClip.mockReturnValueOnce(new Promise<Clip>((r) => { release = r; }));
      getClipShareUrl.mockResolvedValue({ url: "https://example.test/poll.mp4" });
      probeDuration.mockResolvedValue(10); // the new cut, were it ever read
      const onUpdate = vi.fn();
      await mount({ onUpdate });
      await click(button("Trim"));
      await click(step("End", "+2s"));

      await act(async () => root.render(<div />));
      await act(async () => release(echo({ start_time: 10, end_time: 20 })));
      await wait(60_000);
      expect(getClipShareUrl).not.toHaveBeenCalled();
      expect(onUpdate).toHaveBeenCalledTimes(1); // the bounds, never media
      expect(onUpdate).toHaveBeenCalledWith("clip-1", { start_time: 10, end_time: 20 });
    });
  });

  it("starts no poll for a step back that settles after the dialog closed", async () => {
    // R7-M1 with the chain kept (R9-M1): the unmount stops the step out's
    // watch, and the step back, settling after it, must not start the chain's
    // watch again.
    await withTimers(async (wait) => {
      let release!: (c: Clip) => void;
      trimClip
        .mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }))
        .mockReturnValueOnce(new Promise<Clip>((r) => { release = r; }));
      getClipShareUrl.mockResolvedValue({ url: "https://example.test/poll.mp4" });
      probeDuration.mockResolvedValue(8);
      const onUpdate = vi.fn();
      await mount({ onUpdate });
      await click(button("Trim"));
      await click(step("End", "+2s"));
      await click(step("End", "−2s"));

      await act(async () => root.render(<div />));
      await act(async () => release(echo({ start_time: 10, end_time: 18 })));
      await wait(60_000);
      expect(getClipShareUrl).not.toHaveBeenCalled();
      expect(onUpdate).toHaveBeenCalledTimes(2); // the bounds, twice; never media
      expect(onUpdate).toHaveBeenLastCalledWith("clip-1", { start_time: 10, end_time: 18 });
    });
  });

  it("drops a reading that arrives after the dialog closed", async () => {
    await withTimers(async (wait) => {
      let measured!: (s: number) => void;
      trimClip.mockResolvedValueOnce(echo({ start_time: 10, end_time: 20 }));
      getClipShareUrl.mockResolvedValue({ url: "https://example.test/poll.mp4" });
      probeDuration.mockReturnValueOnce(new Promise<number>((r) => { measured = r; }));
      const onUpdate = vi.fn();
      await mount({ onUpdate });
      await click(button("Trim"));
      await click(step("End", "+2s"));
      await wait(3_000);
      expect(probeDuration).toHaveBeenCalledTimes(1);

      await act(async () => root.render(<div />));
      await act(async () => measured(10));
      expect(onUpdate).toHaveBeenCalledTimes(1); // the bounds, never media
    });
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
  it("hides an open panel", async () => {
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

  it("hands focus to the Label toggle when → is pressed on a label chip", async () => {
    // R7-M2: the panel is keyed to the clip, so → hides it and unmounts the
    // focused chip with it; focus fell to <body>, with nothing announced.
    await act(async () => root.render(<Grid initial={[makeClip(), makeClip({ id: "clip-2" })]} />));
    await click(button("Label"));
    act(() => chip("dig").focus());
    expect(document.activeElement).toBe(chip("dig"));

    press("ArrowRight");
    expect(gridRows[1].id).toBe("clip-2");
    expect(document.getElementById("clip-edit-label")).toBeNull(); // the premise: → navigated and hid the panel
    expect(document.activeElement).toBe(button("Label"));
  });

  /** A step in the trim panel, which the keyboard tests only need to focus. */
  function trimStep(): HTMLButtonElement {
    return document.querySelector<HTMLButtonElement>('[aria-label="End of clip"] button')!;
  }

  it("hands focus to the Trim toggle when → is pressed on a trim step", async () => {
    await act(async () => root.render(<Grid initial={[makeClip(), makeClip({ id: "clip-2" })]} />));
    await click(button("Trim"));
    act(() => trimStep().focus());

    press("ArrowRight");
    expect(gridRows[1].id).toBe("clip-2");
    expect(document.getElementById("clip-edit-trim")).toBeNull();
    expect(document.activeElement).toBe(button("Trim"));
  });

  it("hands focus to Label instead where the next clip's Trim cannot take it", async () => {
    // Trim is disabled once the source has expired, and not there on a demo
    // clip; focus on it would go to <body> all the same.
    await act(async () =>
      root.render(
        <Grid
          initial={[
            makeClip({ id: "demo", is_sample: true }),
            makeClip({ id: "clip-2" }),
            makeClip({ id: "expired", source_available: false }),
          ]}
        />,
      ),
    );
    press("ArrowRight"); // onto clip-2, from the dialog card
    await click(button("Trim"));
    act(() => trimStep().focus());

    press("ArrowRight"); // the expired clip
    expect(button("Trim")!.disabled).toBe(true);
    expect(document.activeElement).toBe(button("Label"));

    press("ArrowLeft"); // back to clip-2: its panel shows again, and focus is left where it was
    expect(document.getElementById("clip-edit-trim")).not.toBeNull();
    expect(document.activeElement).toBe(button("Label"));

    act(() => trimStep().focus());
    press("ArrowLeft"); // the demo clip
    expect(button("Trim")).toBeUndefined();
    expect(document.activeElement).toBe(button("Label"));
  });

  it("hands focus to Label when → is pressed on the Trim toggle and the next clip has none", async () => {
    // R9-N7: a demo clip has no Trim button, so the focused toggle unmounts
    // with the clip and focus fell to <body>, as it did from inside a panel.
    await act(async () =>
      root.render(<Grid initial={[makeClip(), makeClip({ id: "demo", is_sample: true })]} />),
    );
    act(() => button("Trim")!.focus());
    expect(document.activeElement).toBe(button("Trim"));

    press("ArrowRight");
    expect(button("Trim")).toBeUndefined();
    expect(document.activeElement).toBe(button("Label"));
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
