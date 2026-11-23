// @vitest-environment jsdom
//
// The upload form. jsdom runs no Tailwind, so where a case is about a colour
// it pins the class name and the rendered result is checked by eye.
//
// CF-385: the "Remove dead time" checkbox takes its tint from the brand token.
// It used to read an arbitrary-value accent over a CSS variable defined
// nowhere, so its indigo #6366f1 fallback always rendered in both themes.
//
// CF-70: the attempt lives in the upload store, so this form must show an
// upload that is already running when it mounts, and redirect only on a
// completion it watched happen — never on one that landed while the user was
// elsewhere.
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Game, UploadTicket } from "@/lib/api";
import type { TransferProgress } from "@/lib/upload";
import { FALLBACK_UPLOAD_CONFIG } from "@/lib/uploadLimits";

const mocks = vi.hoisted(() => ({
  // One shared fn, so the assertion sees the same `push` the component holds;
  // a fresh vi.fn() per useRouter() call would never record anything.
  push: vi.fn(),
  createUpload: vi.fn(),
  completeUpload: vi.fn(),
  deleteGame: vi.fn(),
  uploadFileToR2: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: ComponentProps<"a">) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
vi.mock("@/lib/api", () => ({
  getUploadConfig: () => Promise.resolve(FALLBACK_UPLOAD_CONFIG),
  createUpload: mocks.createUpload,
  completeUpload: mocks.completeUpload,
  deleteGame: mocks.deleteGame,
}));
// Mocked so no real XHR is attempted: jsdom's would fire and fail the run.
vi.mock("@/lib/upload", () => ({ uploadFileToR2: mocks.uploadFileToR2 }));
vi.mock("@/lib/gamesCache", () => ({ addGameToCache: vi.fn() }));
// The real store, with startUpload wrapped so a case can count the calls the
// form makes into it.
vi.mock("@/lib/uploadStore", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/uploadStore")>();
  return { ...real, startUpload: vi.fn(real.startUpload) };
});

import { UploadZone } from "@/components/UploadZone";
import { getUploadSnapshot, resetUploadStore, startUpload } from "@/lib/uploadStore";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ticket: UploadTicket = {
  game_id: "g1",
  mode: "single",
  content_type: "video/mp4",
  expires_in: 3600,
  upload_url: "https://r2.example/put",
  upload_id: null,
  part_size_bytes: null,
  parts: [],
};
const game: Game = {
  id: "g1",
  title: "Varsity vs Lincoln",
  status: "queued",
  progress: 0,
  progress_stage: null,
  created_at: "2026-03-25T00:00:00Z",
};
const opts = { title: "Varsity vs Lincoln", condense: false, duration: null };
const mp4 = () => new File(["x".repeat(100)], "match.mp4", { type: "video/mp4" });

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let host: HTMLDivElement;
let root: Root;
const realCreateObjectURL = URL.createObjectURL;
const realRevokeObjectURL = URL.revokeObjectURL;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.createUpload.mockResolvedValue(ticket);
  mocks.completeUpload.mockResolvedValue(game);
  mocks.deleteGame.mockResolvedValue(undefined);
  mocks.uploadFileToR2.mockResolvedValue([]);
  // readDuration needs object URLs, which jsdom does not implement; without
  // these the duration probe rejects unhandled and fails the run.
  URL.createObjectURL = vi.fn(() => "blob:test");
  URL.revokeObjectURL = vi.fn();
  // jsdom never loads video metadata, so the probe always ends on its 10 s
  // timeout. Fake timers let the case run that out instead of waiting.
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  URL.createObjectURL = realCreateObjectURL;
  URL.revokeObjectURL = realRevokeObjectURL;
  vi.useRealTimers();
  vi.restoreAllMocks();
  // The store is module state and outlives each case by design.
  resetUploadStore();
});

async function render() {
  await act(async () => {
    root.render(<UploadZone />);
  });
}

async function pickMp4() {
  await render();
  const input = host.querySelector<HTMLInputElement>("#file-input");
  expect(input).not.toBeNull();
  Object.defineProperty(input, "files", { value: [mp4()] });
  await act(async () => {
    input!.dispatchEvent(new Event("change", { bubbles: true }));
  });
  // Settle the duration probe so no state update lands after unmount.
  await act(async () => {
    await vi.runOnlyPendingTimersAsync();
  });
}

/** Start an upload before mounting and hold its transfer at `loaded`/100. */
async function holdUploadAt(loaded: number) {
  const transfer = deferred<never[]>();
  mocks.uploadFileToR2.mockImplementation(
    (_f: File, _t: UploadTicket, onProgress: (p: TransferProgress) => void) => {
      onProgress({ loaded, total: 100 });
      return transfer.promise;
    },
  );
  const attempt = startUpload(mp4(), opts);
  await act(async () => {}); // let createUpload resolve
  return { transfer, attempt };
}

const uploadButton = () =>
  Array.from(host.querySelectorAll("button")).find((b) => b.getAttribute("class")?.includes("w-full"));

describe("UploadZone dead-time checkbox (CF-385)", () => {
  it("is tinted with the brand token, not a raw CSS variable", async () => {
    await pickMp4();

    const checkbox = host.querySelector<HTMLInputElement>('input[type="checkbox"]');
    expect(checkbox).not.toBeNull();
    expect(checkbox!.className.split(/\s+/)).toContain("accent-brand");
    expect(checkbox!.className).not.toContain("var(--");
  });
});

describe("UploadZone and the upload store (CF-70)", () => {
  it("mounted mid-upload shows the running upload, not an empty drop zone", async () => {
    // The user left for the Library and came back.
    const { transfer, attempt } = await holdUploadAt(40);

    await render();

    expect(host.textContent).not.toContain("Drop video here");
    expect(host.textContent).toContain("match.mp4");
    expect(host.textContent).toContain("40%");
    expect(host.querySelector<HTMLInputElement>("#file-input")!.disabled).toBe(true);
    expect(host.querySelector<HTMLInputElement>("#game-title")!.value).toBe("Varsity vs Lincoln");
    expect(host.querySelector<HTMLInputElement>("#game-title")!.disabled).toBe(true);
    expect(uploadButton()?.disabled).toBe(true);

    transfer.resolve([]);
    await act(async () => { await attempt; });
  });

  it("mounted after completion offers the game instead of redirecting to it", async () => {
    await act(async () => { await startUpload(mp4(), opts); });
    expect(getUploadSnapshot().phase).toBe("done");

    await render();

    const status = host.querySelector('[role="status"]');
    expect(status).not.toBeNull();
    expect(status!.querySelector('a[href="/games/g1"]')).not.toBeNull();
    expect(mocks.push).not.toHaveBeenCalled();
    // The form is free for the next upload, with no bar left under it: the
    // block above says the upload landed.
    expect(host.textContent).toContain("Drop video here");
    expect(host.querySelector<HTMLInputElement>("#file-input")!.disabled).toBe(false);
    expect(host.textContent).not.toContain("100%");
    // The same row UploadStatus draws (R5-M1): the title alone may be clipped,
    // the message beside it neither shrinks nor wraps from `sm` up, so a long
    // title cannot leave "processing has st…" in a 512 px column.
    const title = status!.querySelector<HTMLElement>(".truncate");
    expect(title).not.toBeNull();
    expect(title!.className.split(/\s+/)).toContain("min-w-0");
    expect(title!.textContent).toBe("\u201cVarsity vs Lincoln\u201d");
    const message = Array.from(status!.querySelectorAll<HTMLElement>("span")).find(
      (s) => s.className.includes("sm:shrink-0"),
    );
    expect(message).toBeDefined();
    expect(message!.className.split(/\s+/)).toContain("sm:whitespace-nowrap");
    expect(message!.textContent).toBe(" uploaded \u2014 processing has started.");
    expect(title!.contains(message!)).toBe(false);
  });

  it("redirects exactly once on a completion it watched", async () => {
    const { transfer, attempt } = await holdUploadAt(40);
    await render();
    expect(mocks.push).not.toHaveBeenCalled();

    transfer.resolve([]);
    await act(async () => { await attempt; });

    expect(mocks.push).toHaveBeenCalledTimes(1);
    expect(mocks.push).toHaveBeenCalledWith("/games/g1");
    // Acknowledged: the status strip must not repeat it on the game page.
    expect(getUploadSnapshot().phase).toBe("idle");
    // And the form stays locked, and named, for the instant before the route
    // changes. There is no local file here — the user left and came back — so
    // this is the store's view of the attempt surviving its own dismissal,
    // not the card collapsing to the empty drop zone with a 100% bar under it.
    expect(host.textContent).not.toContain("Drop video here");
    expect(host.textContent).toContain("match.mp4");
    expect(host.querySelector<HTMLInputElement>("#file-input")!.disabled).toBe(true);
    expect(host.querySelector<HTMLInputElement>("#game-title")!.value).toBe("Varsity vs Lincoln");
    expect(host.querySelector<HTMLInputElement>("#game-title")!.disabled).toBe(true);
    expect(uploadButton()?.disabled).toBe(true);
    expect(host.textContent).toContain("Uploaded");
    expect(host.textContent).toContain("100%");
  });

  it("mounted after a failure shows the error, in the form's own error block", async () => {
    mocks.uploadFileToR2.mockRejectedValue(new Error("Network error during upload"));
    await act(async () => { await startUpload(mp4(), opts); });

    await render();

    expect(host.textContent).toContain("Network error during upload");
    // Pinned by class, as above: the per-theme danger token, not red-400.
    expect(host.querySelector(".text-danger")).not.toBeNull();
    expect(host.querySelector(".text-red-400")).toBeNull();
    // On DESIGN.md's tokens, as UploadStatus's failed row is: a plain surface
    // fill, no palette red, no border around it, no arbitrary px type (R11-M2).
    const block = host.querySelector(".text-danger")!;
    const classes = block.className.split(/\s+/);
    expect(classes).toContain("bg-surface");
    expect(classes.filter((c) => /^border(-|$)/.test(c))).toEqual([]);
    expect(block.className).not.toMatch(/red-\d/);
    expect(block.className).not.toMatch(/text-\[\d+px\]/);
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("an invalid pick still answers the previous outcome rather than showing both", async () => {
    await act(async () => { await startUpload(mp4(), opts); });
    await render();
    expect(host.querySelector('[role="status"]')).not.toBeNull();

    const input = host.querySelector<HTMLInputElement>("#file-input")!;
    Object.defineProperty(input, "files", {
      value: [new File(["x"], "notes.txt", { type: "text/plain" })],
    });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });

    expect(host.textContent).toContain("Unsupported file type");
    expect(host.querySelector('[role="status"]')).toBeNull();
    expect(getUploadSnapshot().phase).toBe("idle");
  });

  it("submitting hands the attempt to the store", async () => {
    await pickMp4();

    await act(async () => { uploadButton()!.click(); });

    expect(mocks.createUpload).toHaveBeenCalledTimes(1);
    expect(mocks.createUpload.mock.calls[0][0]).toMatchObject({ title: "match", filename: "match.mp4" });
    expect(mocks.push).toHaveBeenCalledWith("/games/g1");
  });

  it("a click that lands after the store moved, before the re-render, starts nothing", async () => {
    // The live re-entry guard: this render still says idle, the store does
    // not. Without the guard the form calls startUpload into an attempt in
    // flight, which rejects unawaited.
    await pickMp4();
    const transfer = deferred<never[]>();
    mocks.uploadFileToR2.mockReturnValue(transfer.promise);

    let attempt!: Promise<void>;
    await act(async () => {
      attempt = startUpload(mp4(), opts);
      uploadButton()!.click();
    });

    expect(startUpload).toHaveBeenCalledTimes(1);
    expect(getUploadSnapshot().phase).toBe("uploading");

    transfer.resolve([]);
    await act(async () => { await attempt; });
  });

  it("picking a new file clears last time's outcome", async () => {
    await act(async () => { await startUpload(mp4(), opts); });
    await pickMp4();

    expect(getUploadSnapshot().phase).toBe("idle");
    expect(host.querySelector('[role="status"]')).toBeNull();
  });
});
