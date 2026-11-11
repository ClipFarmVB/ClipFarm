// @vitest-environment jsdom
//
// CF-70: the upload in flight stays visible after leaving /upload, and its
// outcome — the game, or the error — is reported where the user is rather
// than on a page they left, with no redirect.
//
// The store is the real module, driven through `startUpload` with the api and
// transfer mocked, so each case shows what a user would see in that phase.
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Game, UploadTicket } from "@/lib/api";
import type { TransferProgress } from "@/lib/upload";

const mocks = vi.hoisted(() => ({
  pathname: "/games",
  auth: { user: { id: "user-a" } as { id: string } | null, loading: false },
  push: vi.fn(),
  createUpload: vi.fn(),
  completeUpload: vi.fn(),
  deleteGame: vi.fn(),
  uploadFileToR2: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
  useRouter: () => ({ push: mocks.push }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: ComponentProps<"a">) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/lib/api", () => ({
  createUpload: mocks.createUpload,
  completeUpload: mocks.completeUpload,
  deleteGame: mocks.deleteGame,
}));
vi.mock("@/lib/upload", () => ({ uploadFileToR2: mocks.uploadFileToR2 }));
vi.mock("@/lib/gamesCache", () => ({ addGameToCache: vi.fn() }));

import { UploadStatus } from "@/components/UploadStatus";
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

beforeEach(() => {
  vi.clearAllMocks();
  mocks.pathname = "/games";
  mocks.auth = { user: { id: "user-a" }, loading: false };
  mocks.createUpload.mockResolvedValue(ticket);
  mocks.completeUpload.mockResolvedValue(game);
  mocks.deleteGame.mockResolvedValue(undefined);
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  resetUploadStore();
});

async function render() {
  await act(async () => {
    root.render(<UploadStatus />);
  });
}

/** Start an upload and hold its transfer at `loaded` of 100 bytes. */
async function holdUploadAt(loaded: number) {
  const transfer = deferred<never[]>();
  mocks.uploadFileToR2.mockImplementation(
    (_f: File, _t: UploadTicket, onProgress: (p: TransferProgress) => void) => {
      onProgress({ loaded, total: 100 });
      return transfer.promise;
    },
  );
  const attempt = startUpload(new File(["x".repeat(100)], "match.mp4", { type: "video/mp4" }), opts);
  // Let createUpload resolve so the transfer is the step in flight.
  await act(async () => {});
  return { transfer, attempt };
}

async function finishUpload() {
  mocks.uploadFileToR2.mockResolvedValue([]);
  await act(async () => {
    await startUpload(new File(["x"], "match.mp4", { type: "video/mp4" }), opts);
  });
}

async function failUpload(message: string) {
  mocks.uploadFileToR2.mockRejectedValue(new Error(message));
  await act(async () => {
    await startUpload(new File(["x"], "match.mp4", { type: "video/mp4" }), opts);
  });
}

const dismissButton = () =>
  Array.from(host.querySelectorAll("button")).find((b) => b.textContent === "Dismiss");

/**
 * The row's two parts (R5-M1): the title in the one span that may be
 * clipped, and the fixed message beside it, which from `sm` up must neither
 * shrink nor wrap — so an ellipsis can only ever land on the title. jsdom
 * paints nothing, so the shape is what the classes pin.
 */
function rowParts(row: Element) {
  const title = row.querySelector<HTMLElement>(".truncate");
  const message = Array.from(row.querySelectorAll<HTMLElement>("span")).find(
    (s) => s.className.includes("sm:shrink-0"),
  );
  return { title, message };
}

function expectTitleOnlyTruncates(row: Element, message: string) {
  const parts = rowParts(row);
  expect(parts.title).not.toBeNull();
  expect(parts.title!.className.split(/\s+/)).toContain("min-w-0");
  expect(parts.title!.textContent).toBe("\u201cVarsity vs Lincoln\u201d");
  expect(parts.message).toBeDefined();
  expect(parts.message!.className.split(/\s+/)).toContain("sm:whitespace-nowrap");
  expect(parts.message!.textContent).toBe(message);
  expect(parts.title!.contains(parts.message!)).toBe(false);
}

/** No arbitrary pixel type anywhere in `el`: DESIGN.md's floor is 12px, set by scale. */
function expectTokenTypeOnly(el: Element) {
  for (const node of [el, ...Array.from(el.querySelectorAll("*"))]) {
    expect(node.getAttribute("class") ?? "").not.toMatch(/text-\[\d+px\]/);
  }
}

/**
 * The failed row on DESIGN.md's tokens: `danger` text on a plain `surface`
 * fill, with no Tailwind palette red anywhere in it and no border around the
 * surface (Tokens; Borders: "a surface is a fill or nothing"). R11-M2.
 */
function expectTokenDangerRow(el: Element) {
  const classes = el.className.split(/\s+/);
  expect(classes).toContain("text-danger");
  expect(classes).toContain("bg-surface");
  expect(classes.filter((c) => /^border(-|$)/.test(c))).toEqual([]);
  for (const node of [el, ...Array.from(el.querySelectorAll("*"))]) {
    expect(node.getAttribute("class") ?? "").not.toMatch(/(^|[\s:])(bg|border|text|ring)-red-/);
  }
  expectTokenTypeOnly(el);
}

describe("UploadStatus", () => {
  it("renders nothing when no upload is active", async () => {
    await render();

    expect(host.innerHTML).toBe("");
  });

  it("renders nothing on /upload, where UploadZone shows the same upload", async () => {
    mocks.pathname = "/upload";
    const { transfer, attempt } = await holdUploadAt(40);

    await render();

    expect(host.innerHTML).toBe("");
    transfer.resolve([]);
    await act(async () => { await attempt; });
  });

  it("renders nothing while the session is still resolving", async () => {
    // A user object left over while auth refreshes must not flash the strip.
    mocks.auth = { user: { id: "user-a" }, loading: true };
    const { transfer, attempt } = await holdUploadAt(40);

    await render();

    expect(host.innerHTML).toBe("");
    transfer.resolve([]);
    await act(async () => { await attempt; });
  });

  it("renders nothing while signed out", async () => {
    mocks.auth = { user: null, loading: false };
    const { transfer, attempt } = await holdUploadAt(40);

    await render();

    expect(host.innerHTML).toBe("");
    transfer.resolve([]);
    await act(async () => { await attempt; });
  });

  it("shows the title and the percent on another page mid-upload (AC1)", async () => {
    const { transfer, attempt } = await holdUploadAt(40);

    await render();

    const strip = host.firstElementChild;
    expect(strip).not.toBeNull();
    expect(strip!.textContent).toContain("Varsity vs Lincoln");
    // The number carries its noun (DESIGN.md, Voice).
    expect(strip!.textContent).toContain("40% sent");
    // Only the phase is live: the percent and ETA change all transfer long and
    // would be read out over whatever page the user is on (R11-N1).
    const live = strip!.querySelectorAll("[aria-live]");
    expect(live).toHaveLength(1);
    expect(live[0].textContent).toContain("Uploading");
    // No percent in it. Checked by the sign, not by "no digits": a real title
    // ("Club 16U · Saturday pool play") carries digits of its own (R12-N1).
    expect(live[0].textContent).not.toContain("%");
    expectTokenTypeOnly(strip!);
    expect(strip!.textContent).toContain("Uploading…");
    // The same thin brand bar as UploadZone and the game page.
    const bar = strip!.querySelector<HTMLElement>(".bg-brand");
    expect(bar?.style.width).toBe("40%");
    // A long title must not take the ETA with it: only the title truncates.
    expectTitleOnlyTruncates(strip!, " \u00b7 Uploading\u2026");
    // Nothing to dismiss mid-flight: the attempt cannot be stopped.
    expect(dismissButton()).toBeUndefined();

    transfer.resolve([]);
    await act(async () => { await attempt; });
  });

  it("follows the transfer's progress while mounted", async () => {
    const transfer = deferred<never[]>();
    let report!: (p: TransferProgress) => void;
    mocks.uploadFileToR2.mockImplementation(
      (_f: File, _t: UploadTicket, onProgress: (p: TransferProgress) => void) => {
        report = onProgress;
        onProgress({ loaded: 10, total: 100 });
        return transfer.promise;
      },
    );
    const attempt = startUpload(new File(["x"], "match.mp4", { type: "video/mp4" }), opts);
    await render();
    expect(host.textContent).toContain("10%");

    await act(async () => { report({ loaded: 75, total: 100 }); });
    expect(host.textContent).toContain("75%");

    transfer.resolve([]);
    await act(async () => { await attempt; });
    expect(host.querySelector('[role="status"]')).not.toBeNull();
  });

  it("on completion reports it in place with a link to the game, and never navigates (AC2)", async () => {
    await finishUpload();

    await render();

    const status = host.querySelector('[role="status"]');
    expect(status).not.toBeNull();
    expect(status!.textContent).toContain("Varsity vs Lincoln");
    expect(status!.textContent).toContain("processing has started");
    expect(status!.querySelector('a[href="/games/g1"]')).not.toBeNull();
    expect(mocks.push).not.toHaveBeenCalled();
    // The fixed brand tint, not an ad hoc alpha of the brand (DESIGN.md, Tints
    // are fixed), and no type under the 12px floor.
    expect(status!.className.split(/\s+/)).toContain("bg-brand-dim");
    expect(status!.className).not.toMatch(/(bg|border)-brand\//);
    expectTokenTypeOnly(status!);
    // A long title must not eat "processing has started": only it truncates.
    expectTitleOnlyTruncates(status!, " uploaded \u2014 processing has started.");
  });

  it("opening the game from the strip acknowledges the upload", async () => {
    await finishUpload();
    await render();
    // jsdom has no page navigation; the click's default would log an error.
    host.addEventListener("click", (e) => e.preventDefault());

    await act(async () => {
      host.querySelector<HTMLAnchorElement>('a[href="/games/g1"]')!.click();
    });

    expect(getUploadSnapshot().phase).toBe("idle");
    expect(host.innerHTML).toBe("");
  });

  it("reports a failure where the user is, with the message (AC3)", async () => {
    await failUpload("Network error during upload");

    await render();

    const alert = host.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.textContent).toContain("Varsity vs Lincoln");
    expect(alert!.textContent).toContain("Network error during upload");
    // A way back to try again.
    expect(alert!.querySelector('a[href="/upload"]')).not.toBeNull();
    // jsdom runs no Tailwind, so the colour is pinned by class: the per-theme
    // danger token, not red-400, which reads 2.5:1 on the light theme.
    expect(alert!.className).toContain("text-danger");
    expect(alert!.className).not.toContain("text-red-400");
    expectTokenDangerRow(alert!);
    // A filename-derived title is one unbreakable word; without this it would
    // overflow the row and paint across Try again / Dismiss at phone width.
    expect(alert!.querySelector(".break-words")!.textContent).toContain("Varsity vs Lincoln");
    // And nothing in it is clipped: the error is the thing to read in full.
    expect(alert!.querySelector(".truncate")).toBeNull();
  });

  it("dismiss clears the strip", async () => {
    await failUpload("Network error during upload");
    await render();
    expect(dismissButton()).toBeDefined();

    await act(async () => { dismissButton()!.click(); });

    expect(host.innerHTML).toBe("");
    expect(getUploadSnapshot().phase).toBe("idle");
  });
});
