/**
 * A JS implementation of the upload contract (CF-315).
 *
 * It moves no bytes. Its job is to let CF-330 build and demo the whole upload
 * screen — progress, cancellation, failure, retry — before either native half
 * exists, and to stand in wherever a native module cannot be linked (Expo Go, a
 * simulator build predating CF-323/CF-324).
 *
 * Deliberately faithful about the awkward parts of the real thing: parts
 * complete one at a time and in order, ETags only appear as parts land, and a
 * `completedParts` resume starts from the byte count those parts represent
 * rather than from zero.
 */
import type {
  BackgroundUploadModule,
  CompletedPart,
  StartUploadRequest,
  UploadFailure,
  UploadSubscription,
  UploadTask,
} from "./types";
import { totalBytesFor } from "./types";

export interface MockUploadOptions {
  /** How often a progress event fires. */
  tickIntervalMs?: number;
  /** Fraction of the total sent per tick — 0.1 means ten ticks to finish. */
  fractionPerTick?: number;
  /**
   * Fail once this fraction has been sent, instead of completing. For
   * exercising the retry path without a flaky network.
   */
  failAt?: { fraction: number; failure: UploadFailure };
  /**
   * Start every upload in `waiting` rather than `running`, as a wifi-only
   * upload on cellular does (CF-327). `resume()` releases it.
   */
  startWaiting?: boolean;
}

interface MockTask extends UploadTask {
  request: StartUploadRequest;
  timer: ReturnType<typeof setInterval> | null;
}

export interface MockBackgroundUpload extends BackgroundUploadModule {
  /** Release uploads parked by `startWaiting`. */
  resume(uploadId: string): void;
  /** Drop all state — for a test or a screen reload. */
  reset(): void;
}

export function createMockBackgroundUpload(
  options: MockUploadOptions = {}
): MockBackgroundUpload {
  const tickIntervalMs = options.tickIntervalMs ?? 250;
  const fractionPerTick = options.fractionPerTick ?? 0.05;

  const tasks = new Map<string, MockTask>();
  const listeners = new Set<(task: UploadTask) => void>();

  const snapshot = (task: MockTask): UploadTask => ({
    uploadId: task.uploadId,
    state: task.state,
    bytesTransferred: task.bytesTransferred,
    totalBytes: task.totalBytes,
    completedParts: [...task.completedParts],
    ...(task.failure ? { failure: task.failure } : {}),
  });

  function emit(task: MockTask): void {
    const value = snapshot(task);
    // A listener that throws is a bug in one screen, not a reason to strand
    // every other listener — and never a reason to stop a transfer.
    for (const listener of listeners) {
      try {
        listener(value);
      } catch {
        // Ignored on purpose. See above.
      }
    }
  }

  function stop(task: MockTask): void {
    if (task.timer !== null) {
      clearInterval(task.timer);
      task.timer = null;
    }
  }

  /** Which parts the byte count sent so far has fully covered. */
  function partsUpTo(request: StartUploadRequest, bytes: number): CompletedPart[] {
    const done: CompletedPart[] = [];
    let offset = 0;
    for (const part of request.parts) {
      offset += part.end - part.start;
      if (offset > bytes) break;
      done.push({ partNumber: part.partNumber, etag: `mock-etag-${part.partNumber}` });
    }
    return done;
  }

  function tick(task: MockTask): void {
    const step = Math.max(1, Math.ceil(task.totalBytes * fractionPerTick));
    task.bytesTransferred = Math.min(task.totalBytes, task.bytesTransferred + step);

    const failAt = options.failAt;
    if (failAt && task.bytesTransferred >= task.totalBytes * failAt.fraction) {
      stop(task);
      task.state = "failed";
      task.failure = failAt.failure;
      emit(task);
      return;
    }

    task.completedParts = partsUpTo(task.request, task.bytesTransferred);
    if (task.bytesTransferred >= task.totalBytes) {
      stop(task);
      task.state = "completed";
      // Every part lands by definition when the byte count reaches the total;
      // spell it out rather than trusting the arithmetic above to have covered
      // the last, short, part.
      task.completedParts = task.request.parts.map((part) => ({
        partNumber: part.partNumber,
        etag: `mock-etag-${part.partNumber}`,
      }));
    }
    emit(task);
  }

  function run(task: MockTask): void {
    task.state = "running";
    task.timer = setInterval(() => tick(task), tickIntervalMs);
    emit(task);
  }

  return {
    isNative: false,

    async startUpload(request) {
      const existing = tasks.get(request.uploadId);
      if (existing) stop(existing);

      const resumed = request.completedParts ?? [];
      const resumedBytes = request.parts
        .filter((part) => resumed.some((done) => done.partNumber === part.partNumber))
        .reduce((sum, part) => sum + (part.end - part.start), 0);

      const task: MockTask = {
        uploadId: request.uploadId,
        state: "waiting",
        bytesTransferred: resumedBytes,
        totalBytes: totalBytesFor(request),
        completedParts: [...resumed],
        request,
        timer: null,
      };
      tasks.set(request.uploadId, task);

      if (options.startWaiting) {
        emit(task);
      } else {
        run(task);
      }
      return snapshot(task);
    },

    async cancelUpload(uploadId) {
      const task = tasks.get(uploadId);
      // Idempotent: an unknown or already-finished upload is not an error.
      if (!task || task.state === "completed" || task.state === "cancelled") return;
      stop(task);
      task.state = "cancelled";
      emit(task);
    },

    async getUpload(uploadId) {
      const task = tasks.get(uploadId);
      return task ? snapshot(task) : null;
    },

    async getUploads() {
      return [...tasks.values()].map(snapshot);
    },

    addUploadListener(listener): UploadSubscription {
      listeners.add(listener);
      return { remove: () => listeners.delete(listener) };
    },

    resume(uploadId) {
      const task = tasks.get(uploadId);
      if (task && task.state === "waiting") run(task);
    },

    reset() {
      for (const task of tasks.values()) stop(task);
      tasks.clear();
      listeners.clear();
    },
  };
}
