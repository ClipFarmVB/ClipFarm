import { describe, expect, it } from "vitest";
import { apiErrorMessage } from "./apiError";

const FALLBACK = "Upload failed: 500";

describe("apiErrorMessage", () => {
  it("surfaces FastAPI's detail string", () => {
    const body = JSON.stringify({
      detail: "Upload quota reached: 5 of 5 videos in the last 24 hours.",
    });
    expect(apiErrorMessage(body, FALLBACK)).toBe(
      "Upload quota reached: 5 of 5 videos in the last 24 hours."
    );
  });

  it("falls back when the body is not JSON", () => {
    // A proxy or load balancer error page, not our API.
    expect(apiErrorMessage("<html>502 Bad Gateway</html>", FALLBACK)).toBe(FALLBACK);
  });

  it("falls back on an empty body", () => {
    expect(apiErrorMessage("", FALLBACK)).toBe(FALLBACK);
  });

  it("falls back when detail is a validation-error array", () => {
    // 422 puts objects there; rendering them helps nobody.
    const body = JSON.stringify({ detail: [{ loc: ["body", "title"], msg: "required" }] });
    expect(apiErrorMessage(body, FALLBACK)).toBe(FALLBACK);
  });

  it("falls back on a blank detail", () => {
    expect(apiErrorMessage(JSON.stringify({ detail: "   " }), FALLBACK)).toBe(FALLBACK);
  });

  it("ignores a JSON body with no detail field", () => {
    expect(apiErrorMessage(JSON.stringify({ error: "nope" }), FALLBACK)).toBe(FALLBACK);
  });
});
