// @vitest-environment jsdom
//
// CF-565: the demo game's web slice. A copy of the demo game (`is_sample`) is
// marked as a demo, credits its footage as plain text, and does not offer the
// actions the api refuses for it with 409: post, share, download and trim.
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  tagClip: vi.fn(),
  updateClipLabels: vi.fn(),
  trimClip: vi.fn(),
  getClipDownloadUrl: vi.fn(),
  getClipShareUrl: vi.fn(),
  setClipVisibility: vi.fn(),
}));
vi.mock("@/lib/features", () => ({ SOCIAL_ENABLED: true }));
// A signed-in user with a claimed handle, so Post would render if allowed.
vi.mock("@/lib/useMe", () => ({
  useMe: () => ({ id: "u1", username: "alice", username_is_generated: false }),
  needsHandle: () => false,
}));
vi.mock("@/lib/download", () => ({ startCrossOriginDownload: vi.fn() }));
vi.mock("@/components/PostComposerModal", () => ({ PostComposerModal: () => null }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: ComponentProps<"a">) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

import { ClipCard } from "@/components/ClipCard";
import { ClipModal } from "@/components/ClipModal";
import { DemoBanner, DemoCredit } from "@/components/DemoGame";
import type { Clip } from "@/lib/api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function makeClip(isSample: boolean): Clip {
  return {
    id: "clip-1",
    game_id: "game-1",
    player_id: null,
    player_name: null,
    action_type: "spike",
    confidence: 0.9,
    highlight_score: null,
    start_time: 10,
    end_time: 18,
    clip_url: "https://example.test/c.mp4",
    thumbnail_url: "https://example.test/c.jpg",
    labels: [],
    created_at: "2026-01-01T00:00:00Z",
    source_available: true,
    effective_visibility: "private",
    is_sample: isSample,
  } as unknown as Clip;
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  HTMLMediaElement.prototype.load = vi.fn();
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function buttonLabels(scope: ParentNode = document): string[] {
  return [...scope.querySelectorAll("button")].map((b) => (b.textContent ?? "").trim());
}

describe("ClipCard on a demo clip", () => {
  function render(isSample: boolean) {
    act(() => root.render(<ClipCard clip={makeClip(isSample)} players={[]} onPlay={() => {}} />));
  }

  it("does not offer Download or Trim, which the api refuses", () => {
    render(true);
    const labels = buttonLabels(host);

    expect(labels.some((l) => l.includes("Download"))).toBe(false);
    expect(labels.some((l) => l.includes("Trim"))).toBe(false);
    // Label stays: relabelling a demo clip is allowed (it writes no Correction).
    expect(labels.some((l) => l.includes("Label"))).toBe(true);
  });

  it("still offers both on an ordinary clip", () => {
    render(false);
    const labels = buttonLabels(host);

    expect(labels.some((l) => l.includes("Download"))).toBe(true);
    expect(labels.some((l) => l.includes("Trim"))).toBe(true);
  });
});

describe("ClipModal on a demo clip", () => {
  function render(isSample: boolean) {
    act(() => root.render(<ClipModal clip={makeClip(isSample)} onClose={() => {}} ownsClip />));
  }

  it("marks the clip as a demo and offers no Download, Copy link or Post", () => {
    render(true);
    const labels = buttonLabels();

    expect(labels.some((l) => l.includes("Download"))).toBe(false);
    expect(labels.some((l) => l.includes("Copy link"))).toBe(false);
    expect(labels.some((l) => l === "Post")).toBe(false);
    expect(document.body.textContent).toContain("Demo");
  });

  it("offers all three on an ordinary clip", () => {
    render(false);
    const labels = buttonLabels();

    expect(labels.some((l) => l.includes("Download"))).toBe(true);
    expect(labels.some((l) => l.includes("Copy link"))).toBe(true);
    expect(labels.some((l) => l === "Post")).toBe(true);
  });
});

describe("DemoCredit", () => {
  it("renders the credit as text and links only the https URL", () => {
    act(() =>
      root.render(<DemoCredit credit="Footage: Riverside Hawks — https://www.youtube.com/watch?v=abc" />),
    );
    const links = [...host.querySelectorAll("a")];

    expect(host.textContent).toBe("Footage: Riverside Hawks — https://www.youtube.com/watch?v=abc");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://www.youtube.com/watch?v=abc");
    expect(links[0].getAttribute("rel")).toBe("noopener noreferrer");
    expect(links[0].getAttribute("target")).toBe("_blank");
  });

  it("never renders markup from the credit, and never links a javascript: URL", () => {
    act(() => root.render(<DemoCredit credit={'<img src=x onerror="alert(1)"> javascript://alert(1)'} />));

    expect(host.querySelector("img")).toBeNull();
    expect(host.querySelector("a")).toBeNull();
    expect(host.textContent).toContain("<img src=x");
  });

  it("renders nothing when there is no credit", () => {
    act(() => root.render(<DemoCredit credit={null} />));

    expect(host.innerHTML).toBe("");
  });
});

describe("DemoBanner", () => {
  it("says it is a demo, credits the footage, and links to upload", () => {
    act(() => root.render(<DemoBanner credit="Footage: Riverside Hawks" />));

    expect(host.textContent).toContain("This is a demo game");
    expect(host.textContent).toContain("Footage: Riverside Hawks");
    expect(host.querySelector('a[href="/upload"]')?.textContent).toContain("Upload a game");
  });

  it("shows no credit line when none is set", () => {
    act(() => root.render(<DemoBanner credit={null} />));

    expect(host.textContent).not.toContain("Footage");
  });
});
