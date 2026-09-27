// CF-221 moved these out of UploadZone so the onboarding panel quotes the same
// limits the upload form enforces. UploadZone has no tests of its own, so this
// is what notices if the move changed an output.
import { describe, expect, it } from "vitest";
import { FALLBACK_UPLOAD_CONFIG, SUPPORTED_FORMATS_LABEL, fmtLimit, fmtMinutes } from "@/lib/uploadLimits";

describe("fmtLimit", () => {
  it("formats gigabytes with one decimal under 10 GB", () => {
    expect(fmtLimit(8 * 1024 ** 3)).toBe("8 GB");
    expect(fmtLimit(1.5 * 1024 ** 3)).toBe("1.5 GB");
  });

  it("rounds to whole gigabytes from 10 GB", () => {
    expect(fmtLimit(15.4 * 1024 ** 3)).toBe("15 GB");
  });

  it("falls back to megabytes under 1 GB", () => {
    expect(fmtLimit(500 * 1024 ** 2)).toBe("500 MB");
  });
});

describe("fmtMinutes", () => {
  it("formats minutes, whole hours, and mixed", () => {
    expect(fmtMinutes(45)).toBe("45 min");
    expect(fmtMinutes(240)).toBe("4 h");
    expect(fmtMinutes(90)).toBe("1 h 30 min");
  });

  it("clamps negatives to zero and rounds", () => {
    expect(fmtMinutes(-3)).toBe("0 min");
    expect(fmtMinutes(12.6)).toBe("13 min");
  });
});

describe("FALLBACK_UPLOAD_CONFIG", () => {
  it("keeps the api's caps: 8 GB, 4 h", () => {
    expect(fmtLimit(FALLBACK_UPLOAD_CONFIG.max_upload_bytes)).toBe("8 GB");
    expect(fmtMinutes(FALLBACK_UPLOAD_CONFIG.max_duration_seconds / 60)).toBe("4 h");
  });

  it("names one format per allowed content type", () => {
    const types = FALLBACK_UPLOAD_CONFIG.allowed_content_types;
    expect(types).toHaveLength(4);
    expect(SUPPORTED_FORMATS_LABEL.split(", ")).toHaveLength(types.length);
  });
});
