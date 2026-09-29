import { describe, expect, it } from "vitest";
import { splitCredit } from "./credit";

describe("splitCredit", () => {
  it("returns nothing for an empty or missing credit", () => {
    expect(splitCredit(null)).toEqual([]);
    expect(splitCredit(undefined)).toEqual([]);
    expect(splitCredit("   ")).toEqual([]);
  });

  it("keeps a credit with no URL as one text part", () => {
    expect(splitCredit("  Footage: Riverside Hawks ")).toEqual([
      { kind: "text", text: "Footage: Riverside Hawks" },
    ]);
  });

  it("links an https URL and keeps the text around it", () => {
    expect(splitCredit("Footage: Riverside Hawks — https://www.youtube.com/watch?v=abc")).toEqual([
      { kind: "text", text: "Footage: Riverside Hawks — " },
      {
        kind: "link",
        text: "https://www.youtube.com/watch?v=abc",
        href: "https://www.youtube.com/watch?v=abc",
      },
    ]);
  });

  it("leaves trailing punctuation out of the link", () => {
    const parts = splitCredit("See https://example.org/video.");

    expect(parts[1]).toMatchObject({ kind: "link", href: "https://example.org/video" });
    expect(parts[2]).toEqual({ kind: "text", text: "." });
  });

  it.each([
    "javascript://alert(1)",
    "data://text/html,hi",
    "ftp://example.org/file",
  ])("never links a non-http scheme: %s", (value) => {
    const parts = splitCredit(`Credit ${value}`);

    expect(parts.every((p) => p.kind === "text")).toBe(true);
    expect(parts.map((p) => p.text).join("")).toBe(`Credit ${value}`);
  });
});
