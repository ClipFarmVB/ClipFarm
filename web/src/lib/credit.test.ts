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

  it("links a plain http URL too", () => {
    expect(splitCredit("Source: http://example.org/v")).toEqual([
      { kind: "text", text: "Source: " },
      { kind: "link", text: "http://example.org/v", href: "http://example.org/v" },
    ]);
  });

  it("keeps a balanced closing paren and drops an unbalanced one", () => {
    expect(splitCredit("See https://en.wikipedia.org/wiki/Volleyball_(sport)")[1]).toMatchObject({
      href: "https://en.wikipedia.org/wiki/Volleyball_(sport)",
    });
    const wrapped = splitCredit("(https://example.org/v)");
    expect(wrapped[1]).toMatchObject({ kind: "link", href: "https://example.org/v" });
    expect(wrapped[2]).toEqual({ kind: "text", text: ")" });
  });

  it("leaves curly quotes and an ellipsis out of the link", () => {
    expect(splitCredit("“https://a.org/q”")[1]).toMatchObject({ href: "https://a.org/q" });
    expect(splitCredit("https://youtu.be/abc…")[0]).toMatchObject({ href: "https://youtu.be/abc" });
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
