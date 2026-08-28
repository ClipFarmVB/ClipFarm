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

  it("renders a validation-error array as the messages the user can act on", () => {
    // 422 puts {loc, msg, type} objects here. These used to fall back, so a
    // validation failure showed a generic line — and the post composer grew a
    // private copy of this parsing to read them. `msg` is the actionable half;
    // `loc` and `type` are for us and stay out.
    const body = JSON.stringify({
      detail: [{ loc: ["body", "caption"], msg: "String should have at most 500 characters" }],
    });
    expect(apiErrorMessage(body, FALLBACK)).toBe("String should have at most 500 characters");
  });

  it("joins several validation messages", () => {
    const body = JSON.stringify({
      detail: [{ msg: "field required" }, { msg: "must be a valid uuid" }],
    });
    expect(apiErrorMessage(body, FALLBACK)).toBe("field required; must be a valid uuid");
  });

  it("falls back when the array carries nothing renderable", () => {
    // An array of objects with no usable msg is still for us, not the user.
    const body = JSON.stringify({ detail: [{ loc: ["body"], type: "missing" }] });
    expect(apiErrorMessage(body, FALLBACK)).toBe(FALLBACK);
    expect(apiErrorMessage(JSON.stringify({ detail: [] }), FALLBACK)).toBe(FALLBACK);
  });

  it("falls back on a blank detail", () => {
    expect(apiErrorMessage(JSON.stringify({ detail: "   " }), FALLBACK)).toBe(FALLBACK);
  });

  it("ignores a JSON body with no detail field", () => {
    expect(apiErrorMessage(JSON.stringify({ error: "nope" }), FALLBACK)).toBe(FALLBACK);
  });

  it("is not idempotent — decoding an already-decoded message destroys it", () => {
    // Not a property of this function so much as the rule its callers have to
    // know, pinned here because getting it wrong is silent and looked correct
    // in review. `throwApiError` runs a response body through this and throws
    // the RESULT, so a catch block holds the finished sentence: passing it back
    // in makes JSON.parse throw and returns the fallback, replacing a real
    // explanation with a generic one.
    //
    // PostComposerModal did exactly that, so the 409 naming the clip's
    // visibility ceiling — the backstop for a clip that goes private between
    // page load and click, the one case the greyed-out tiers cannot cover —
    // always reached the user as "Could not post". The 422 branch above made it
    // worse rather than better: a first decode that now succeeds is a second
    // decode that now fails.
    //
    // The rule: decode once, at the throw site. A catch block uses e.message.
    const body = JSON.stringify({ detail: "This clip is private." });
    const once = apiErrorMessage(body, FALLBACK);
    expect(once).toBe("This clip is private.");
    expect(apiErrorMessage(once, "Could not post")).toBe("Could not post");
  });
});
