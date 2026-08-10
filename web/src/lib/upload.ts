/**
 * Browser → R2 transfer (CF-163).
 *
 * The api hands out presigned URLs and never touches the video, so everything
 * about moving the bytes — slicing, retrying, measuring progress — happens
 * here.  Kept out of api.ts because none of it involves our own API.
 *
 * XHR rather than fetch: fetch still has no upload-progress event, and the
 * progress bar is a requirement, not a nicety, on a file this size.
 */

import type { CompletedPart, UploadTicket } from "./api";

/** Parts uploaded at once.  Enough to keep a fast link busy, few enough that a
 *  phone isn't juggling several multi-hundred-MB buffers. */
const CONCURRENCY = 3;
/** Attempts per part, including the first.  A part is the retry unit — this is
 *  the whole reason large uploads are multipart. */
const ATTEMPTS = 3;
const RETRY_BASE_MS = 500;

export interface PartPlan {
  partNumber: number;
  start: number;
  end: number; // exclusive
  size: number;
}

/**
 * Slice boundaries for a multipart upload.
 *
 * The server derives its part count with the same ceil division when it
 * presigns, so the two agree without exchanging offsets — the ticket only
 * carries `part_size_bytes`.  Part numbers are 1-based, as S3 requires.
 */
export function planParts(fileSize: number, partSize: number): PartPlan[] {
  if (partSize <= 0) throw new Error("partSize must be positive");
  // A zero-byte file still gets one (empty) part: S3 rejects a multipart
  // upload that completes with no parts at all.
  const count = Math.max(1, Math.ceil(fileSize / partSize));
  return Array.from({ length: count }, (_, i) => {
    const start = i * partSize;
    const end = Math.min(start + partSize, fileSize);
    return { partNumber: i + 1, start, end, size: Math.max(0, end - start) };
  });
}

export interface TransferProgress {
  loaded: number;
  total: number;
}

export class UploadError extends Error {
  constructor(message: string, readonly retryable: boolean) {
    super(message);
    this.name = "UploadError";
  }
}

/** Only transient conditions are worth a second attempt.  A 403 from an
 *  expired URL or a signature mismatch will fail identically every time, and
 *  retrying it three times per part across three workers just delays the
 *  error the user needs to see. */
function isRetryableStatus(status: number): boolean {
  return status >= 500 || status === 408 || status === 429;
}

/** One PUT with progress reporting.  Resolves with the response ETag when
 *  `requireEtag` (multipart completion needs it); a single PUT does not. */
function put(
  url: string,
  body: Blob,
  contentType: string | null,
  requireEtag: boolean,
  onLoaded: (loaded: number) => void
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    // Only set for a single-shot PUT, where the api signed ContentType into
    // the URL and R2 rejects a mismatch.  Part URLs don't sign it, so we leave
    // the header alone rather than risk contradicting the signature.
    if (contentType) xhr.setRequestHeader("Content-Type", contentType);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onLoaded(e.loaded);
    };
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new UploadError(
          `Storage rejected the upload (${xhr.status})`,
          isRetryableStatus(xhr.status)
        ));
        return;
      }
      onLoaded(body.size); // the last progress event can lag the actual end

      const etag = xhr.getResponseHeader("ETag");
      if (requireEtag && !etag) {
        // Not a transfer failure — a bucket misconfiguration. ETag is not
        // readable cross-origin unless CORS lists it under ExposeHeaders, and
        // without this check every part would resolve to "" and the upload
        // would fail only at assembly, after every byte had moved, with a
        // generic 400 that points nowhere near the actual cause.
        reject(new UploadError(
          "Storage did not return an ETag. The R2 bucket's CORS policy must " +
          "list ETag under ExposeHeaders for uploads to complete.",
          false
        ));
        return;
      }
      resolve(etag ?? "");
    };
    xhr.onerror = () => reject(new UploadError("Network error during upload", true));
    xhr.onabort = () => reject(new UploadError("Upload aborted", false));
    xhr.send(body);
  });
}

async function withRetry<T>(attempt: (n: number) => Promise<T>): Promise<T> {
  let lastErr: unknown;
  for (let n = 0; n < ATTEMPTS; n++) {
    try {
      return await attempt(n);
    } catch (err) {
      lastErr = err;
      if (err instanceof UploadError && !err.retryable) break;
      if (n < ATTEMPTS - 1) {
        await new Promise((r) => setTimeout(r, RETRY_BASE_MS * 2 ** n));
      }
    }
  }
  throw lastErr;
}

/**
 * Upload `file` using `ticket`, reporting aggregate bytes as it goes.
 *
 * Returns the completed parts to hand back to the api (empty for a single
 * PUT).  Progress can dip when a part is retried — that is the truth of what
 * happened, and pretending otherwise would mean a bar that stalls at a number
 * it can't justify.
 */
export async function uploadFileToR2(
  file: File,
  ticket: UploadTicket,
  onProgress?: (p: TransferProgress) => void
): Promise<CompletedPart[]> {
  const total = file.size;

  if (ticket.mode === "single") {
    if (!ticket.upload_url) throw new Error("Upload ticket is missing its URL");
    await withRetry(() =>
      // No ETag needed: a single PUT is the whole object, nothing to assemble.
      put(ticket.upload_url!, file, ticket.content_type, false, (loaded) =>
        onProgress?.({ loaded, total })
      )
    );
    return [];
  }

  const partSize = ticket.part_size_bytes;
  if (!partSize) throw new Error("Upload ticket is missing its part size");
  const plans = planParts(total, partSize);
  const urls = new Map(ticket.parts.map((p) => [p.part_number, p.url]));

  // Bytes confirmed per part, so the aggregate stays correct while several
  // parts are in flight and resets cleanly for one that is being retried.
  const loadedByPart = new Map<number, number>();
  const report = () => {
    let sum = 0;
    for (const v of loadedByPart.values()) sum += v;
    onProgress?.({ loaded: Math.min(sum, total), total });
  };

  const completed: CompletedPart[] = [];
  let next = 0;
  // Once any worker gives up, the others stop claiming new parts instead of
  // uploading into an upload that is already doomed. Requests already in
  // flight still run to completion — cancelling those needs the XHRs to be
  // tracked and aborted, which is more machinery than the saving warrants.
  let failed = false;

  const worker = async () => {
    while (next < plans.length && !failed) {
      const plan = plans[next++];
      const url = urls.get(plan.partNumber);
      if (!url) throw new Error(`Upload ticket is missing part ${plan.partNumber}`);

      try {
        const etag = await withRetry(() => {
          loadedByPart.set(plan.partNumber, 0); // a retry re-sends the whole part
          report();
          return put(url, file.slice(plan.start, plan.end), null, true, (loaded) => {
            loadedByPart.set(plan.partNumber, loaded);
            report();
          });
        });
        completed.push({ part_number: plan.partNumber, etag });
      } catch (err) {
        failed = true;
        throw err;
      }
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(CONCURRENCY, plans.length) }, worker)
  );

  return completed.sort((a, b) => a.part_number - b.part_number);
}
