// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DemoVideo } from "./DemoVideo";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
  vi.unstubAllEnvs();
});

function render() {
  act(() => root.render(<DemoVideo />));
  return host;
}

describe("DemoVideo", () => {
  it.each([
    ["unset", undefined],
    ["blank", "   "],
  ])("shows the placeholder, and no player, when the URL is %s", (_label, value) => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_VIDEO_URL", value);
    const el = render();

    expect(el.querySelector("video")).toBeNull();
    expect(el.querySelector("[data-testid=demo-video-placeholder]")).not.toBeNull();
    expect(el.querySelector("img")).toBeNull();
  });

  it("renders a lazy, click-to-play video when the URL is set", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_VIDEO_URL", "https://media.test/demo.mp4");
    vi.stubEnv("NEXT_PUBLIC_DEMO_POSTER_URL", "");
    const video = render().querySelector("video");

    expect(video).not.toBeNull();
    expect(video!.getAttribute("src")).toBe("https://media.test/demo.mp4");
    expect(video!.getAttribute("preload")).toBe("none");
    expect(video!.hasAttribute("controls")).toBe(true);
    expect(video!.hasAttribute("autoplay")).toBe(false);
    expect(video!.autoplay).toBe(false);
    expect(video!.hasAttribute("poster")).toBe(false);
    expect(host.querySelector("[data-testid=demo-video-placeholder]")).toBeNull();
  });

  it("sets the poster only when one is configured", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_VIDEO_URL", "https://media.test/demo.mp4");
    vi.stubEnv("NEXT_PUBLIC_DEMO_POSTER_URL", "https://media.test/poster.jpg");

    expect(render().querySelector("video")?.getAttribute("poster")).toBe(
      "https://media.test/poster.jpg",
    );
  });
});
