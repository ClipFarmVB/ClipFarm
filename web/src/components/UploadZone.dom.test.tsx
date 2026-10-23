// @vitest-environment jsdom
//
// CF-385: the "Remove dead time" checkbox takes its tint from the brand token.
//
// It used to read an arbitrary-value accent over a CSS variable defined
// nowhere, so its indigo #6366f1 fallback always rendered in both themes.
// jsdom runs no Tailwind, so this case pins the class name; the rendered
// colour is checked by eye.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FALLBACK_UPLOAD_CONFIG } from "@/lib/uploadLimits";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api", () => ({
  getUploadConfig: () => Promise.resolve(FALLBACK_UPLOAD_CONFIG),
  createUpload: vi.fn(),
  completeUpload: vi.fn(),
  deleteGame: vi.fn(),
}));
vi.mock("@/lib/gamesCache", () => ({ addGameToCache: vi.fn() }));

import { UploadZone } from "@/components/UploadZone";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
const realCreateObjectURL = URL.createObjectURL;
const realRevokeObjectURL = URL.revokeObjectURL;

beforeEach(() => {
  // readDuration needs object URLs, which jsdom does not implement; without
  // these the duration probe rejects unhandled and fails the run.
  URL.createObjectURL = vi.fn(() => "blob:test");
  URL.revokeObjectURL = vi.fn();
  // jsdom never loads video metadata, so the probe always ends on its 10 s
  // timeout. Fake timers let the case run that out instead of waiting.
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  URL.createObjectURL = realCreateObjectURL;
  URL.revokeObjectURL = realRevokeObjectURL;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function pickMp4() {
  await act(async () => {
    root.render(<UploadZone />);
  });
  const input = host.querySelector<HTMLInputElement>("#file-input");
  expect(input).not.toBeNull();
  const file = new File(["x"], "match.mp4", { type: "video/mp4" });
  Object.defineProperty(input, "files", { value: [file] });
  await act(async () => {
    input!.dispatchEvent(new Event("change", { bubbles: true }));
  });
  // Settle the duration probe so no state update lands after unmount.
  await act(async () => {
    await vi.runOnlyPendingTimersAsync();
  });
}

describe("UploadZone dead-time checkbox", () => {
  it("is tinted with the brand token, not a raw CSS variable", async () => {
    await pickMp4();

    const checkbox = host.querySelector<HTMLInputElement>('input[type="checkbox"]');
    expect(checkbox).not.toBeNull();
    expect(checkbox!.className.split(/\s+/)).toContain("accent-brand");
    expect(checkbox!.className).not.toContain("var(--");
  });
});
