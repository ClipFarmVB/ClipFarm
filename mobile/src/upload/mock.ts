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
  UploadFailure,
  UploadPartTarget,
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
  /** Parts the caller said were already stored, with the ETags R2 gave them. */
  resumed: CompletedPart[];
  /** Everything else, in order — the only bytes this task actually "sends". */
  pending: UploadPartTarget[];
  resumedBytes: number;
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

  /**
   * The parts stored so far: the resumed ones, plus whichever pending parts the
   * byte count has now covered.
   *
   * Resumed parts are carried through with the ETags they came in with, never
   * re-derived. They are also skipped rather than re-sent, so progress has to
   * be measured against `pending` — attributing bytes to `request.parts` in
   * order would credit part 1 for work done on part 3 whenever the resumed set
   * is not a leading prefix, and hand back an ETag R2 never issued.
   */
  function storedParts(task: MockTask): CompletedPart[] {
    const done = [...task.resumed];
    let offset = task.resumedBytes;
    for (const part of task.pending) {
      offset += part.end - part.start;
      if (offset > task.bytesTransferred) break;
      done.push({ partNumber: part.partNumber, etag: `mock-etag-${part.partNumber}` });
    }
    return done.sort((a, b) => a.partNumber - b.partNumber);
  }

  function tick(task: MockTask): void {
    const step = Math.max(1, Math.ceil(task.totalBytes * fractionPerTick));
    task.bytesTransferred = Math.min(task.totalBytes, task.bytesTransferred + step);
    // Before the failure check, not after: a failed task still has to report
    // the parts that did land, because that set is what the retry resumes from
    // (rule 4 in ./types). Recomputing only on the success path is how a retry
    // ends up re-sending a part R2 already has.
    task.completedParts = storedParts(task);

    const failAt = options.failAt;
    if (failAt && task.bytesTransferred >= task.totalBytes * failAt.fraction) {
      stop(task);
      task.state = "failed";
      task.failure = failAt.failure;
      emit(task);
      return;
    }

    // At the total, `storedParts` has covered every pending part by
    // construction — the offsets it walks sum to exactly `totalBytes`.
    if (task.bytesTransferred >= task.totalBytes) {
      stop(task);
      task.state = "completed";
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
      const isResumed = (part: UploadPartTarget) =>
        resumed.some((done) => done.partNumber === part.partNumber);
      const pending = request.parts.filter((part) => !isResumed(part));
      const resumedBytes = request.parts
        .filter(isResumed)
        .reduce((sum, part) => sum + (part.end - part.start), 0);

      const task: MockTask = {
        uploadId: request.uploadId,
        state: "waiting",
        bytesTransferred: resumedBytes,
        totalBytes: totalBytesFor(request),
        completedParts: [...resumed],
        resumed,
        pending,
        resumedBytes,
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
      // `failed` counts as finished — cancelling one would otherwise leave a
      // task reporting `cancelled` while still carrying a `failure`, which
      // ./types says can only accompany `failed`.
      if (!task || (task.state !== "waiting" && task.state !== "running")) return;
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
