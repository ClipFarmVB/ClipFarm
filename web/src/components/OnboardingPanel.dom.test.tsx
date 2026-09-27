// @vitest-environment jsdom
//
// CF-221: the first-upload onboarding panel on the Library.
//
// The record module keeps a module-level memory fallback that outlives each
// case, so every case uses its own user id.
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Game, UploadConfig } from "@/lib/api";

const getUploadConfig = vi.fn<() => Promise<UploadConfig>>();
vi.mock("@/lib/api", () => ({ getUploadConfig: () => getUploadConfig() }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: ComponentProps<"a">) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

import { OnboardingPanel } from "@/components/OnboardingPanel";
import { onboardingKey } from "@/lib/onboarding";
import { FALLBACK_UPLOAD_CONFIG } from "@/lib/uploadLimits";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function game(id: string, status: Game["status"]): Game {
  return { id, title: id, status, progress: 0, progress_stage: null, created_at: "2026-01-01T00:00:00Z" };
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  getUploadConfig.mockReset();
  getUploadConfig.mockResolvedValue(FALLBACK_UPLOAD_CONFIG);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  localStorage.clear();
  vi.restoreAllMocks();
});

async function render(props: Partial<ComponentProps<typeof OnboardingPanel>> & { userId?: string }) {
  await act(async () => {
    root.render(
      <OnboardingPanel games={[]} loading={false} error={null} {...props} />,
    );
  });
  return host;
}

const panel = () => host.querySelector("section");
const skipButton = () =>
  Array.from(host.querySelectorAll("button")).find((b) => b.textContent === "Skip");

describe("OnboardingPanel", () => {
  it("shows for a new user", async () => {
    await render({ userId: "new-1" });

    expect(panel()).not.toBeNull();
    expect(host.querySelectorAll("li")).toHaveLength(3);
    expect(host.querySelector('a[href="/upload"]')).not.toBeNull();
  });

  it("stays hidden when a record is stored", async () => {
    localStorage.setItem(onboardingKey("stored-1"), "skipped");
    await render({ userId: "stored-1" });
    expect(host.innerHTML).toBe("");

    localStorage.setItem(onboardingKey("stored-2"), "done");
    await render({ userId: "stored-2" });
    expect(host.innerHTML).toBe("");
  });

  it("stays hidden while loading, on a fetch error, and without a user", async () => {
    await render({ userId: "hidden-1", loading: true });
    expect(host.innerHTML).toBe("");

    await render({ userId: "hidden-1", error: "Network down" });
    expect(host.innerHTML).toBe("");

    await render({ userId: undefined });
    expect(host.innerHTML).toBe("");
  });

  it("hides on Skip and remembers it", async () => {
    await render({ userId: "skip-1" });
    const skip = skipButton();
    expect(skip).toBeDefined();

    await act(async () => skip!.click());

    expect(host.innerHTML).toBe("");
    expect(localStorage.getItem(onboardingKey("skip-1"))).toBe("skipped");
  });

  it("still renders and Skip still works when storage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });

    await render({ userId: "throw-1" });
    expect(panel()).not.toBeNull();

    await act(async () => skipButton()!.click());
    expect(host.innerHTML).toBe("");
  });

  it("quotes the server's limits", async () => {
    getUploadConfig.mockResolvedValue({
      ...FALLBACK_UPLOAD_CONFIG,
      max_upload_bytes: 2 * 1024 ** 3,
      max_duration_seconds: 2 * 3600,
    });
    await render({ userId: "limits-1" });

    expect(getUploadConfig).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("up to 2 GB and 2 h");
  });

  it("falls back to the built-in limits when the config call fails", async () => {
    getUploadConfig.mockRejectedValue(new Error("offline"));
    await render({ userId: "limits-2" });

    expect(panel()).not.toBeNull();
    expect(host.textContent).toContain("up to 8 GB and 4 h");
  });

  it("links to the game while it processes", async () => {
    await render({ userId: "proc-1", games: [game("g1", "processing")] });

    expect(host.querySelector('a[href="/games/g1"]')).not.toBeNull();
    // Step one is done, so its upload link is gone.
    expect(host.querySelector('a[href="/upload"]')).toBeNull();
  });

  it("completes silently once the user's own game is ready", async () => {
    await render({ userId: "ready-1", games: [game("g1", "ready")] });

    expect(host.innerHTML).toBe("");
    expect(localStorage.getItem(onboardingKey("ready-1"))).toBe("done");
  });

  it("does not count the sample game as the user's own", async () => {
    await render({ userId: "sample-1", games: [game("sample", "ready")], sampleGameId: "sample" });

    expect(panel()).not.toBeNull();
    expect(localStorage.getItem(onboardingKey("sample-1"))).toBeNull();
    expect(host.querySelector('a[href="/games/sample"]')?.textContent).toBe("See an example");
  });

  it("has no example link without a sample game", async () => {
    await render({ userId: "nosample-1" });

    expect(panel()).not.toBeNull();
    expect(host.textContent).not.toContain("See an example");
  });
});
