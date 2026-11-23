// @vitest-environment jsdom
//
// CF-423 (#556): a collection that does not exist and a server that broke are
// different answers, and the page must say which one it got. The API 404s a
// collection that is missing or not the caller's (`_get_owned_collection`);
// before the status reached the client, both rendered as the same red line.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getCollections = vi.hoisted(() => vi.fn());
const getCollectionClips = vi.hoisted(() => vi.fn());
const getPlayers = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  getCollections,
  getCollectionClips,
  getPlayers,
  removeClipFromCollection: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "col-1" }) }));
vi.mock("@/components/RequireAuth", () => ({
  RequireAuth: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("next/link", () => ({
  default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
vi.mock("@/components/ClipCard", () => ({ ClipCard: () => null }));
vi.mock("@/components/ClipModal", () => ({ ClipModal: () => null }));
vi.mock("@/components/CollectionPickerModal", () => ({ CollectionPickerModal: () => null }));

import { ApiError } from "@/lib/apiError";
import CollectionPage from "@/app/collections/[id]/page";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  getCollections.mockResolvedValue([]);
  getPlayers.mockResolvedValue([]);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
});

async function renderFailure(rejection: unknown) {
  getCollectionClips.mockRejectedValue(rejection);
  await act(async () => {
    root.render(<CollectionPage />);
  });
}

describe("a collection that failed to load", () => {
  it("says the collection was not found on a 404, with a way back", async () => {
    await renderFailure(new ApiError("Collection not found", 404));

    expect(host.querySelector("h1")?.textContent).toBe("Collection not found");
    expect(host.querySelector('a[href="/collections"]')).not.toBeNull();
  });

  it("shows a server fault as a fault, not as a missing collection", async () => {
    await renderFailure(new ApiError("Internal Server Error", 500));

    expect(host.textContent).toContain("Internal Server Error");
    expect(host.textContent).not.toContain("Collection not found");
  });

  it("does not throw inside its catch on a non-Error rejection", async () => {
    // `e.message` on `null` throws inside the catch; the guard keeps the
    // page on its own fallback line instead.
    await renderFailure(null);

    expect(host.textContent).toContain("Couldn't load this collection.");
  });
});
