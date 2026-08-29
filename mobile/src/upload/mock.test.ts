import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createMockBackgroundUpload } from "./mock";
import type { StartUploadRequest, UploadTask } from "./types";

const PART_SIZE = 1000;

function request(overrides: Partial<StartUploadRequest> = {}): StartUploadRequest {
  return {
    uploadId: "game-1",
    fileUri: "file:///tmp/game.mp4",
    contentType: null,
    parts: [
      { partNumber: 1, url: "https://r2.example/1", start: 0, end: PART_SIZE },
      { partNumber: 2, url: "https://r2.example/2", start: PART_SIZE, end: PART_SIZE * 2 },
      { partNumber: 3, url: "https://r2.example/3", start: PART_SIZE * 2, end: PART_SIZE * 3 },
    ],
    ...overrides,
  };
}

describe("createMockBackgroundUpload", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("reports progress and finishes with an ETag for every part", async () => {
    const upload = createMockBackgroundUpload({ fractionPerTick: 0.5 });
    const seen: UploadTask[] = [];
    upload.addUploadListener((task) => seen.push(task));

    await upload.startUpload(request());
    await vi.advanceTimersByTimeAsync(1000);

    const final = await upload.getUpload("game-1");
    expect(final?.state).toBe("completed");
    expect(final?.bytesTransferred).toBe(PART_SIZE * 3);
    expect(final?.completedParts.map((p) => p.partNumber)).toEqual([1, 2, 3]);
    expect(final?.completedParts.every((p) => p.etag)).toBe(true);
    // Snapshots, not deltas: every event stands on its own.
    expect(seen.every((task) => task.totalBytes === PART_SIZE * 3)).toBe(true);
    expect(seen.at(-1)?.state).toBe("completed");
  });

  it("resumes from the parts already stored", async () => {
    const upload = createMockBackgroundUpload({ fractionPerTick: 0.5 });

    const started = await upload.startUpload(
      request({ completedParts: [{ partNumber: 1, etag: "etag-1" }] })
    );

    // The retry path after an expired URL: the bytes already in R2 are not
    // sent again, which is what keeps a 4 GB retry from starting over.
    expect(started.bytesTransferred).toBe(PART_SIZE);
    expect(started.totalBytes).toBe(PART_SIZE * 3);
  });

  it("fails at the configured point instead of completing", async () => {
    const upload = createMockBackgroundUpload({
      fractionPerTick: 0.25,
      failAt: { fraction: 0.5, failure: { reason: "expired-url", message: "410" } },
    });

    await upload.startUpload(request());
    await vi.advanceTimersByTimeAsync(2000);

    const task = await upload.getUpload("game-1");
    expect(task?.state).toBe("failed");
    expect(task?.failure?.reason).toBe("expired-url");
  });

  it("stops a cancelled upload and stays cancelled", async () => {
    const upload = createMockBackgroundUpload({ fractionPerTick: 0.1 });

    await upload.startUpload(request());
    await vi.advanceTimersByTimeAsync(250);
    await upload.cancelUpload("game-1");
    const atCancel = await upload.getUpload("game-1");
    await vi.advanceTimersByTimeAsync(5000);

    const later = await upload.getUpload("game-1");
    expect(later?.state).toBe("cancelled");
    expect(later?.bytesTransferred).toBe(atCancel?.bytesTransferred);
  });

  it("treats cancelling an unknown upload as a no-op", async () => {
    const upload = createMockBackgroundUpload();
    await expect(upload.cancelUpload("never-started")).resolves.toBeUndefined();
  });

  it("parks a wifi-only upload until it is released", async () => {
    const upload = createMockBackgroundUpload({ startWaiting: true, fractionPerTick: 0.5 });

    await upload.startUpload(request({ wifiOnly: true }));
    await vi.advanceTimersByTimeAsync(2000);
    expect((await upload.getUpload("game-1"))?.state).toBe("waiting");

    upload.resume("game-1");
    await vi.advanceTimersByTimeAsync(2000);
    expect((await upload.getUpload("game-1"))?.state).toBe("completed");
  });

  it("keeps delivering to other listeners when one throws", async () => {
    const upload = createMockBackgroundUpload({ fractionPerTick: 1 });
    const seen: UploadTask[] = [];
    upload.addUploadListener(() => {
      throw new Error("a screen blew up");
    });
    upload.addUploadListener((task) => seen.push(task));

    await upload.startUpload(request());
    await vi.advanceTimersByTimeAsync(500);

    expect(seen.at(-1)?.state).toBe("completed");
  });

  it("lists what it is still tracking, as a launch would ask", async () => {
    const upload = createMockBackgroundUpload({ fractionPerTick: 0.5 });

    await upload.startUpload(request());
    await upload.startUpload(request({ uploadId: "game-2" }));

    expect((await upload.getUploads()).map((t) => t.uploadId).sort()).toEqual([
      "game-1",
      "game-2",
    ]);
  });
});
