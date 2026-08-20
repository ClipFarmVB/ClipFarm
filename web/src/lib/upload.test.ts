/**
 * CF-163: the browser → R2 transfer.
 *
 * This leg cannot be exercised against a real bucket from CI, so these are the
 * only thing standing behind it. A fake XHR covers the failure modes that cost
 * a whole upload when they go wrong.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { UploadTicket } from "./api";
import { planParts, uploadFileToR2 } from "./upload";

describe("planParts", () => {
  it("uses 1-based part numbers, as S3 requires", () => {
    expect(planParts(250, 100).map((p) => p.partNumber)).toEqual([1, 2, 3]);
  });

  it("covers the file exactly, with no gaps or overlap", () => {
    const size = 1_234_567;
    const parts = planParts(size, 100_000);
    expect(parts[0].start).toBe(0);
    expect(parts[parts.length - 1].end).toBe(size);
    for (let i = 1; i < parts.length; i++) {
      expect(parts[i].start).toBe(parts[i - 1].end);
    }
    expect(parts.reduce((n, p) => n + p.size, 0)).toBe(size);
  });

  it("does not emit a trailing empty part when the size divides evenly", () => {
    const parts = planParts(300, 100);
    expect(parts).toHaveLength(3);
    expect(parts.every((p) => p.size === 100)).toBe(true);
  });

  it("leaves the remainder in a short final part", () => {
    const parts = planParts(250, 100);
    expect(parts.map((p) => p.size)).toEqual([100, 100, 50]);
  });

  it("still produces one part for an empty file", () => {
    // S3 rejects a multipart upload completed with no parts at all.
    expect(planParts(0, 100)).toHaveLength(1);
  });

  it("agrees with the server's part count at realistic sizes", () => {
    // Mirrors storage.plan_part_count in api/tests/test_uploads.py.
    const partSize = 100 * 1024 ** 2;
    expect(planParts(2 * 1024 ** 3, partSize)).toHaveLength(21);
    expect(planParts(partSize, partSize)).toHaveLength(1);
    expect(planParts(partSize + 1, partSize)).toHaveLength(2);
  });

  it("rejects a non-positive part size rather than looping forever", () => {
    expect(() => planParts(100, 0)).toThrow();
  });
});

// ── Fake XHR ─────────────────────────────────────────────────────────────────
// Each send() consumes the next queued response, so a test scripts a sequence
// of outcomes ("part 1 fails with 500, then succeeds") without touching timing.

interface ScriptedResponse {
  status?: number;
  etag?: string | null;   // null = header absent (CORS not exposing ETag)
  networkError?: boolean;
}

interface RecordedRequest {
  url: string;
  headers: Record<string, string>;
  bodySize: number;
}

let scripted: ScriptedResponse[] = [];
let sent: RecordedRequest[] = [];

class FakeXHR {
  upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  status = 200;

  private url = "";
  private headers: Record<string, string> = {};
  private etag: string | null = '"abc123"';

  open(_method: string, url: string) { this.url = url; }
  setRequestHeader(k: string, v: string) { this.headers[k] = v; }
  getResponseHeader(name: string) { return name === "ETag" ? this.etag : null; }

  send(body: { size: number }) {
    const cfg = scripted.shift() ?? {};
    sent.push({ url: this.url, headers: { ...this.headers }, bodySize: body.size });
    this.status = cfg.status ?? 200;
    this.etag = cfg.etag === undefined ? '"abc123"' : cfg.etag;

    queueMicrotask(() => {
      if (cfg.networkError) { this.onerror?.(); return; }
      this.upload.onprogress?.({
        lengthComputable: true, loaded: body.size, total: body.size,
      } as ProgressEvent);
      this.onload?.();
    });
  }
}

/** A File stand-in: uploadFileToR2 only ever reads .size and .slice(). */
function fakeFile(size: number): File {
  return {
    size,
    slice: (start: number, end: number) => ({ size: end - start }),
  } as unknown as File;
}

function multipartTicket(parts: number, partSize: number): UploadTicket {
  return {
    game_id: "game-1",
    mode: "multipart",
    content_type: "video/mp4",
    expires_in: 3600,
    upload_url: null,
    upload_id: "upload-1",
    part_size_bytes: partSize,
    parts: Array.from({ length: parts }, (_, i) => ({
      part_number: i + 1,
      url: `https://r2.test/key?partNumber=${i + 1}`,
    })),
  };
}

function singleTicket(): UploadTicket {
  return {
    game_id: "game-1",
    mode: "single",
    content_type: "video/mp4",
    expires_in: 3600,
    upload_url: "https://r2.test/key?sig=single",
    upload_id: null,
    part_size_bytes: null,
    parts: [],
  };
}

/** Let workers still running after Promise.all rejected finish their turn.
 *  Without this, `sent` is read while the siblings are mid-flight and any
 *  assertion about them stopping passes regardless of whether they did. */
function drain() {
  return new Promise((r) => setTimeout(r, 100));
}

describe("uploadFileToR2", () => {
  beforeEach(() => {
    scripted = [];
    sent = [];
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("fails immediately and diagnosably when R2 does not expose the ETag", async () => {
    // The default CORS config does not expose ETag. Without this check every
    // part resolves to "", the bar climbs to 99%, and the upload only fails at
    // assembly — after every byte has moved — with a generic 400.
    scripted = [{ etag: null }];
    await expect(
      uploadFileToR2(fakeFile(200), multipartTicket(2, 100))
    ).rejects.toThrow(/ExposeHeaders/);
  });

  it("does not retry a missing ETag — it is config, not a transient failure", async () => {
    scripted = [{ etag: null }, { etag: null }, { etag: null }];
    await uploadFileToR2(fakeFile(100), multipartTicket(1, 100)).catch(() => {});
    expect(sent).toHaveLength(1);
  });

  it("does not require an ETag for a single PUT", async () => {
    scripted = [{ etag: null }];
    await expect(uploadFileToR2(fakeFile(50), singleTicket())).resolves.toEqual([]);
  });

  it("sends the signed Content-Type on a single PUT but not on parts", async () => {
    await uploadFileToR2(fakeFile(50), singleTicket());
    expect(sent[0].headers["Content-Type"]).toBe("video/mp4");

    sent = [];
    await uploadFileToR2(fakeFile(100), multipartTicket(1, 100));
    // Part URLs don't sign ContentType; sending one risks contradicting the
    // signature, so the header must be left alone.
    expect(sent[0].headers["Content-Type"]).toBeUndefined();
  });

  it("retries a failed part and re-sends the whole part", async () => {
    scripted = [{ status: 500 }, {}];
    const parts = await uploadFileToR2(fakeFile(100), multipartTicket(1, 100));

    expect(sent).toHaveLength(2);
    expect(sent[0].bodySize).toBe(100);
    expect(sent[1].bodySize).toBe(100); // the retry re-sends everything
    expect(parts).toEqual([{ part_number: 1, etag: '"abc123"' }]);
  });

  it("gives up immediately on a permanent failure", async () => {
    // A 403 from an expired URL or signature mismatch fails identically every
    // time; retrying it just delays the error the user needs to see.
    scripted = [{ status: 403 }];
    await uploadFileToR2(fakeFile(100), multipartTicket(1, 100)).catch(() => {});
    expect(sent).toHaveLength(1);
  });

  it("retries a network error", async () => {
    scripted = [{ networkError: true }, {}];
    await uploadFileToR2(fakeFile(100), multipartTicket(1, 100));
    expect(sent).toHaveLength(2);
  });

  it("never reports progress above the file size, with parts in flight", async () => {
    const size = 500;
    const seen: number[] = [];
    await uploadFileToR2(fakeFile(size), multipartTicket(5, 100), (p) => {
      seen.push(p.loaded);
      expect(p.total).toBe(size);
    });
    expect(Math.max(...seen)).toBeLessThanOrEqual(size);
    expect(seen.at(-1)).toBe(size);
  });

  it("returns every part, sorted, regardless of completion order", async () => {
    const parts = await uploadFileToR2(fakeFile(450), multipartTicket(5, 100));
    expect(parts.map((p) => p.part_number)).toEqual([1, 2, 3, 4, 5]);
  });

  it("stops claiming new parts when a part URL is missing from the ticket", async () => {
    // This guard used to throw from outside the try, so it never set the
    // failure flag and the other workers kept uploading into a doomed upload.
    const ticket = multipartTicket(10, 100);
    ticket.parts = ticket.parts.filter((p) => p.part_number !== 1);

    await uploadFileToR2(fakeFile(1000), ticket).catch(() => {});
    await drain();
    // 9 parts have URLs; if the siblings kept going they would all be sent.
    expect(sent.length).toBeLessThan(9);
  });

  it("stops claiming new parts once one has failed", async () => {
    // 10 parts, 3 workers. Part 1 fails permanently; the rest must not keep
    // uploading into an upload that is already doomed.
    scripted = [{ status: 403 }];
    await uploadFileToR2(fakeFile(1000), multipartTicket(10, 100)).catch(() => {});
    await drain();
    expect(sent.length).toBeLessThan(10);
  });
});
