// CF-70: the upload attempt lives in a module-level store so it outlives the
// upload page. These cases pin what each acceptance criterion needs from the
// store itself; the two component suites pin what is drawn from it.
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Game, UploadTicket } from "./api";
import type { TransferProgress } from "./upload";

const mocks = vi.hoisted(() => ({
  createUpload: vi.fn(),
  completeUpload: vi.fn(),
  deleteGame: vi.fn(),
  uploadFileToR2: vi.fn(),
  addGameToCache: vi.fn(),
}));

vi.mock("./api", () => ({
  createUpload: mocks.createUpload,
  completeUpload: mocks.completeUpload,
  deleteGame: mocks.deleteGame,
}));
vi.mock("./upload", () => ({ uploadFileToR2: mocks.uploadFileToR2 }));
vi.mock("./gamesCache", () => ({ addGameToCache: mocks.addGameToCache }));

import {
  dismissUpload,
  getUploadSnapshot,
  resetUploadStore,
  startUpload,
  subscribeUpload,
  type UploadSnapshot,
} from "./uploadStore";

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

const file = () => new File(["x".repeat(100)], "match.mp4", { type: "video/mp4" });
const opts = { title: "Varsity vs Lincoln", condense: false, duration: null };

/** A promise this test settles itself, to hold the transfer in flight. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Every snapshot published from now on, in order. */
function record(): UploadSnapshot[] {
  const seen: UploadSnapshot[] = [];
  subscribeUpload(() => seen.push(getUploadSnapshot()));
  return seen;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.createUpload.mockResolvedValue(ticket);
  mocks.completeUpload.mockResolvedValue(game);
  mocks.deleteGame.mockResolvedValue(undefined);
  mocks.uploadFileToR2.mockResolvedValue([]);
});

afterEach(() => {
  // Module state: a reset also orphans anything a case left in flight.
  resetUploadStore();
});

describe("startUpload", () => {
  it("publishes the transfer's progress as it happens (AC1)", async () => {
    mocks.uploadFileToR2.mockImplementation(
      async (_f: File, _t: UploadTicket, onProgress: (p: TransferProgress) => void) => {
        onProgress({ loaded: 40, total: 100 });
        return [];
      },
    );
    const seen = record();

    await startUpload(file(), opts);

    const phases = seen.map((s) => s.phase);
    expect(phases).toEqual(["uploading", "uploading", "finalizing", "done"]);
    // The bar shows at once, not on the first onprogress event.
    expect(seen[0]).toMatchObject({ progress: 0, fileName: "match.mp4", sizeBytes: 100, title: opts.title });
    expect(seen[1].progress).toBe(40);
    expect(seen[2].statusText).toBe("Finalizing…");
  });

  it("holds the bar under 100 until completion has returned", async () => {
    mocks.uploadFileToR2.mockImplementation(
      async (_f: File, _t: UploadTicket, onProgress: (p: TransferProgress) => void) => {
        onProgress({ loaded: 100, total: 100 });
        return [];
      },
    );
    const seen = record();

    await startUpload(file(), opts);

    expect(seen.find((s) => s.phase === "uploading" && s.progress! > 0)?.progress).toBe(99);
    expect(getUploadSnapshot().progress).toBe(100);
  });

  it("on completion writes the game into the cache and publishes its id (AC2)", async () => {
    await startUpload(file(), opts);

    expect(mocks.completeUpload).toHaveBeenCalledWith("g1", []);
    expect(mocks.addGameToCache).toHaveBeenCalledWith(game);
    expect(getUploadSnapshot()).toMatchObject({ phase: "done", gameId: "g1", progress: 100 });
  });

  it("passes the form's choices through to the ticket request", async () => {
    await startUpload(file(), { title: "Finals", condense: true, duration: 4200 });

    expect(mocks.createUpload).toHaveBeenCalledWith({
      title: "Finals",
      filename: "match.mp4",
      content_type: "video/mp4",
      size_bytes: 100,
      condense: true,
      duration_seconds: 4200,
    });
  });

  it("publishes a transfer failure with its message and discards the row (AC3)", async () => {
    mocks.uploadFileToR2.mockRejectedValue(new Error("Network error during upload"));

    await startUpload(file(), opts);

    expect(getUploadSnapshot()).toMatchObject({
      phase: "failed",
      error: "Network error during upload",
      progress: null,
      title: opts.title,
    });
    expect(mocks.deleteGame).toHaveBeenCalledWith("g1");
    expect(mocks.addGameToCache).not.toHaveBeenCalled();
  });

  it("does not discard the row once completion is in flight", async () => {
    // complete_upload claims the row and dispatches the job before it
    // responds; a lost response may well be a success, and deleting here would
    // remove a game the worker is processing.
    mocks.completeUpload.mockRejectedValue(new Error("API error 504"));

    await startUpload(file(), opts);

    expect(getUploadSnapshot()).toMatchObject({ phase: "failed", error: "API error 504" });
    expect(mocks.deleteGame).not.toHaveBeenCalled();
  });

  it("does not discard anything when no ticket was issued", async () => {
    mocks.createUpload.mockRejectedValue(new Error("File is 3.0 GB; the maximum is 2.0 GB."));

    await startUpload(file(), opts);

    expect(getUploadSnapshot().error).toBe("File is 3.0 GB; the maximum is 2.0 GB.");
    expect(mocks.deleteGame).not.toHaveBeenCalled();
  });

  it("refuses a second upload while one is in flight", async () => {
    const transfer = deferred<never[]>();
    mocks.uploadFileToR2.mockReturnValue(transfer.promise);
    const first = startUpload(file(), opts);

    await expect(startUpload(file(), { ...opts, title: "Second" })).rejects.toThrow(/already in progress/);
    // The refusal left the first attempt untouched.
    expect(getUploadSnapshot()).toMatchObject({ phase: "uploading", title: opts.title });
    expect(mocks.createUpload).toHaveBeenCalledTimes(1);

    transfer.resolve([]);
    await first;
    expect(getUploadSnapshot().phase).toBe("done");
  });

  it("allows a retry after a failure", async () => {
    mocks.uploadFileToR2.mockRejectedValueOnce(new Error("boom"));
    await startUpload(file(), opts);
    expect(getUploadSnapshot().phase).toBe("failed");

    await startUpload(file(), opts);

    expect(getUploadSnapshot()).toMatchObject({ phase: "done", error: null });
  });
});

describe("resetUploadStore", () => {
  it("silences an attempt in flight: no publish, no completion, no cache write", async () => {
    // An identity change mid-upload. The XHR cannot be aborted, so the
    // continuation runs; it must not complete the game under the new session
    // or show the previous user's upload to this one.
    const transfer = deferred<never[]>();
    mocks.uploadFileToR2.mockReturnValue(transfer.promise);
    const attempt = startUpload(file(), opts);
    expect(getUploadSnapshot().phase).toBe("uploading");

    resetUploadStore();
    const seen = record();
    transfer.resolve([]);
    await attempt;

    expect(seen).toEqual([]);
    expect(getUploadSnapshot().phase).toBe("idle");
    expect(mocks.completeUpload).not.toHaveBeenCalled();
    expect(mocks.addGameToCache).not.toHaveBeenCalled();
  });

  it("silences a completion that returns after the reset: no cache write, no publish", async () => {
    // Same identity change, one step later: the bytes had landed and
    // `completeUpload` was already in flight. Its response must not write the
    // game into the new user's cache or publish `done` to them.
    const completion = deferred<Game>();
    mocks.completeUpload.mockReturnValue(completion.promise);
    const attempt = startUpload(file(), opts);
    await vi.waitFor(() => expect(getUploadSnapshot().phase).toBe("finalizing"));

    resetUploadStore();
    const seen = record();
    completion.resolve(game);
    await attempt;

    expect(seen).toEqual([]);
    expect(getUploadSnapshot().phase).toBe("idle");
    expect(mocks.addGameToCache).not.toHaveBeenCalled();
  });

  it("silences a failure that lands after the reset", async () => {
    const transfer = deferred<never[]>();
    mocks.uploadFileToR2.mockReturnValue(transfer.promise);
    const attempt = startUpload(file(), opts);

    resetUploadStore();
    transfer.reject(new Error("Network error during upload"));
    await attempt;

    expect(getUploadSnapshot().phase).toBe("idle");
  });

  it("lets a new upload start after a reset while the old one is still moving", async () => {
    const transfer = deferred<never[]>();
    mocks.uploadFileToR2.mockReturnValueOnce(transfer.promise);
    const orphan = startUpload(file(), opts);

    resetUploadStore();
    await startUpload(file(), { ...opts, title: "Next user's game" });
    expect(getUploadSnapshot()).toMatchObject({ phase: "done", title: "Next user's game" });

    // The orphan settling afterwards must not overwrite the new user's result.
    transfer.resolve([]);
    await orphan;
    expect(getUploadSnapshot()).toMatchObject({ phase: "done", title: "Next user's game" });
  });
});

describe("dismissUpload", () => {
  it("clears a finished upload", async () => {
    await startUpload(file(), opts);

    dismissUpload();

    expect(getUploadSnapshot().phase).toBe("idle");
  });

  it("clears a failed upload", async () => {
    mocks.uploadFileToR2.mockRejectedValue(new Error("boom"));
    await startUpload(file(), opts);

    dismissUpload();

    expect(getUploadSnapshot()).toMatchObject({ phase: "idle", error: null });
  });

  it("is a no-op mid-flight", async () => {
    const transfer = deferred<never[]>();
    mocks.uploadFileToR2.mockReturnValue(transfer.promise);
    const attempt = startUpload(file(), opts);

    dismissUpload();
    expect(getUploadSnapshot().phase).toBe("uploading");

    transfer.resolve([]);
    await attempt;
    expect(getUploadSnapshot().phase).toBe("done");
  });
});

describe("module boundaries", () => {
  it("never imports next/navigation — navigation is the upload page's call", () => {
    const source = readFileSync(new URL("./uploadStore.ts", import.meta.url), "utf8");
    // Import statements only — the docblock is allowed to name the rule.
    expect(source).not.toMatch(/import[^;]*["']next\/navigation["']/);
  });
});
