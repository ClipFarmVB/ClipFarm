// @vitest-environment jsdom
//
// CF-304 (#354): "A failed fetch renders an error, never an affirmative 'you
// have none'." The empty-state body already carried `!error`; the header
// subtitle did not, so a failed `getCollections` put "No collections yet"
// directly above the red error card.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getCollections = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  getCollections,
  createCollection: vi.fn(),
  renameCollection: vi.fn(),
  deleteCollection: vi.fn(),
}));
vi.mock("@/components/RequireAuth", () => ({
  RequireAuth: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import CollectionsPage from "@/app/collections/page";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
});

describe("the collections page", () => {
  it("does not say there are no collections when the load failed", async () => {
    getCollections.mockRejectedValue(new Error("Network down"));
    await act(async () => {
      root.render(<CollectionsPage />);
    });

    expect(host.textContent).toContain("Network down");
    expect(host.textContent).not.toContain("No collections yet");
  });

  it("still says so when the load succeeded with none", async () => {
    getCollections.mockResolvedValue([]);
    await act(async () => {
      root.render(<CollectionsPage />);
    });

    expect(host.textContent).toContain("No collections yet");
  });
});
