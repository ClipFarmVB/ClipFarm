/**
 * The background-upload contract (CF-315).
 *
 * Defined here, up front, because three tickets build against it and none of
 * them can wait on the others: CF-323 implements it in Swift, CF-324 in Kotlin,
 * and CF-330 builds the upload screen against the mock in ./mock.
 *
 * Five rules that the two native halves have to agree on, and which are not
 * obvious from the type signatures:
 *
 * 1. **The module never talks to the ClipFarm API.** JS calls
 *    `POST /games/uploads` for the ticket and `POST /games/{id}/uploads/complete`
 *    afterwards; the module only PUTs bytes at the presigned URLs it is handed
 *    and reports the ETags back. Auth, quota and every api route therefore stay
 *    in one place instead of being reimplemented in two languages.
 *
 * 2. **`uploadId` is chosen by the caller** — it is the game id — and is stable
 *    across a relaunch. The system can kill and restart the app mid-transfer
 *    (routine on iOS), and on the next launch JS asks `getUploads()` what
 *    survived. An id minted by the native side would be lost with the process
 *    that minted it.
 *
 * 3. **Progress is a snapshot, not a delta.** Every event carries the whole
 *    task, so a listener that attached late — or after a relaunch — is correct
 *    immediately, with no accumulated state to rebuild.
 *
 * 4. **Presigned URLs expire.** `url_ttl_seconds` is minutes; a multi-gigabyte
 *    upload over a gym's wifi is not. `expired-url` is its own failure reason
 *    because its recovery is specific: JS re-issues the ticket and calls
 *    `startUpload` again with the parts already done in `completedParts`, and
 *    the module skips them. That is also what makes a failed upload retryable
 *    without creating a second game (CF-330).
 *
 * 5. **Temp files are the module's to clean up**, on success, failure and
 *    cancellation alike. iOS cannot upload from memory or a byte range at all —
 *    every part has to be written to disk first — so a 4 GB video can leave 4 GB
 *    of debris behind on any path that forgets.
 */

/** One presigned destination, and the slice of the file that goes to it. */
export interface UploadPartTarget {
  /** 1-based, as S3 requires. */
  partNumber: number;
  url: string;
  /** Byte offset into the source file. */
  start: number;
  /** Exclusive end offset. `end - start` is the part size. */
  end: number;
}

/** What `POST /games/{id}/uploads/complete` needs back for each part. */
export interface CompletedPart {
  partNumber: number;
  etag: string;
}

export interface StartUploadRequest {
  /** The game id. Stable across a relaunch — see rule 2 above. */
  uploadId: string;
  /**
   * The picked video. `file://` on iOS; the Android photo picker hands back a
   * `content://` URI, which the module resolves itself (CF-324) rather than
   * making every caller do it.
   */
  fileUri: string;
  /**
   * Set only for a single-shot PUT, where the api signed ContentType into the
   * URL and R2 rejects a mismatch. Part URLs do not sign it, so the header must
   * be left alone rather than contradicting the signature.
   */
  contentType: string | null;
  parts: UploadPartTarget[];
  /** Parts already stored — skipped, and echoed back in the finished task. */
  completedParts?: CompletedPart[];
  /**
   * Hold the transfer while the device is on cellular (CF-327). The task stays
   * `waiting` rather than failing, and resumes when wifi returns.
   */
  wifiOnly?: boolean;
  /**
   * Android requires a visible notification for the foreground service that
   * keeps a long upload alive. Ignored on iOS, where the OS owns the UI.
   */
  notification?: {
    title: string;
    body: string;
  };
}

export type UploadState =
  | "waiting"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

/**
 * Why an upload stopped. The set is small on purpose — each member exists
 * because the *recovery* differs, not to describe the error more precisely.
 */
export type UploadFailureReason =
  /** Transient: connectivity, a 5xx, a timeout. Retry as-is. */
  | "network"
  /** A presigned URL is past its TTL. Re-issue the ticket, then resume. */
  | "expired-url"
  /** The picked file is gone or unreadable — the user must pick again. */
  | "file-unreadable"
  /** R2 or the api rejected the transfer in a way retrying will not fix. */
  | "rejected"
  | "unknown";

export interface UploadFailure {
  reason: UploadFailureReason;
  /** For logs and bug reports, not for display. */
  message: string;
}

/** A whole-task snapshot. Every event carries one — see rule 3. */
export interface UploadTask {
  uploadId: string;
  state: UploadState;
  bytesTransferred: number;
  totalBytes: number;
  /** Grows as parts land; complete when `state` is `completed`. */
  completedParts: CompletedPart[];
  /** Present only when `state` is `failed`. */
  failure?: UploadFailure;
}

export interface UploadSubscription {
  remove(): void;
}

export interface BackgroundUploadModule {
  /**
   * False when the JS mock is standing in — no native module is linked, which
   * is every Expo Go session and every simulator build made before CF-323 and
   * CF-324 land. A caller that must not fake a transfer (a real upload screen
   * in a production build) checks this; CF-330 does not, which is the point.
   */
  readonly isNative: boolean;

  /**
   * Begin, or resume with `completedParts`. Resolves once the transfer is
   * handed to the OS, not when it finishes — the app may not be running by
   * then. Watch `addUploadListener` for the rest.
   */
  startUpload(request: StartUploadRequest): Promise<UploadTask>;

  /** Idempotent: cancelling an unknown or finished upload is not an error. */
  cancelUpload(uploadId: string): Promise<void>;

  /** Null when the module has never heard of it, or has forgotten it. */
  getUpload(uploadId: string): Promise<UploadTask | null>;

  /**
   * Everything the module is still tracking, including transfers the system
   * continued while the app was dead. This is the first call on launch.
   */
  getUploads(): Promise<UploadTask[]>;

  /** Fires on progress and on every state change. */
  addUploadListener(listener: (task: UploadTask) => void): UploadSubscription;
}

/**
 * The name the native halves register under, and the single event they emit.
 * Both are part of the contract: `requireOptionalNativeModule` in ./index looks
 * for exactly this.
 */
export const NATIVE_MODULE_NAME = "ClipFarmUpload";
export const UPLOAD_EVENT = "onUploadChange";

/** Total bytes across the parts still to send plus those already done. */
export function totalBytesFor(request: StartUploadRequest): number {
  return request.parts.reduce((sum, part) => sum + (part.end - part.start), 0);
}
