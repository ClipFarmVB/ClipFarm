/**
 * CF-163: multipart slice planning.
 *
 * The server presigns `ceil(size / partSize)` part URLs and the client slices
 * the file itself — the two never exchange offsets, they just have to agree.
 * A disagreement here means a part with no URL, or an assembled object that
 * silently omits the tail, so the arithmetic is worth pinning down.
 */
import { describe, expect, it } from "vitest";

import { planParts } from "./upload";

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
