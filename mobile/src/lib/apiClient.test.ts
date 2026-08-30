import { describe, expect, it } from "vitest";

import { errorMessage } from "./apiClient";

/**
 * Kept in step with `web/src/lib/apiError.ts` — the two show the same sentence
 * for the same response until CF-314 makes them literally the same function.
 */
describe("errorMessage", () => {
  it("uses the server's own sentence", () => {
    const body = JSON.stringify({
      detail: "you have 60 min of your 360 min per 24 hours left",
    });
    expect(errorMessage(body, "fallback")).toBe(
      "you have 60 min of your 360 min per 24 hours left"
    );
  });

  it("reads a 422's validation messages", () => {
    // FastAPI's other `detail` shape. Left unhandled it falls back, and the
    // user sees raw JSON for the most ordinary kind of rejection there is.
    const body = JSON.stringify({
      detail: [
        { loc: ["body", "caption"], msg: "String should have at most 500 characters", type: "x" },
        { loc: ["body", "title"], msg: "Field required", type: "y" },
      ],
    });
    expect(errorMessage(body, "fallback")).toBe(
      "String should have at most 500 characters; Field required"
    );
  });

  it("falls back on a body that is not JSON", () => {
    expect(errorMessage("<html>502 Bad Gateway</html>", "API error 502")).toBe("API error 502");
  });

  it("falls back on JSON with nothing usable in detail", () => {
    expect(errorMessage(JSON.stringify({ detail: [] }), "API error 422")).toBe("API error 422");
    expect(errorMessage(JSON.stringify({ detail: "   " }), "API error 400")).toBe("API error 400");
  });
});
