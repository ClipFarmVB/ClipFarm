// @vitest-environment jsdom
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: ComponentProps<"a">) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: null, loading: false }),
}));

import HomePage, { metadata } from "./page";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(<HomePage />));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const headings = () => Array.from(host.querySelectorAll("h2")).map((h) => h.textContent);

describe("landing page", () => {
  it("has one h1 saying what the product does", () => {
    const h1s = host.querySelectorAll("h1");

    expect(h1s).toHaveLength(1);
    expect(h1s[0].textContent).toContain("highlight");
  });

  it("points its primary call to action at sign-up", () => {
    const signup = Array.from(host.querySelectorAll("a")).filter(
      (a) => a.getAttribute("href") === "/signup",
    );

    expect(signup.length).toBeGreaterThan(0);
    expect(signup[0].textContent).toContain("Get started");
    expect(host.querySelector('a[href="/login"]')).not.toBeNull();
  });

  it("has the demo video, how-it-works and sample-output slots", () => {
    expect(headings()).toEqual(
      expect.arrayContaining(["Demo video", "How it works", "What you get"]),
    );
    expect(host.querySelector("[data-testid=demo-video-placeholder]")).not.toBeNull();
  });

  it("labels the sample clips as examples", () => {
    expect(host.textContent).toContain("Example clips");
  });

  it("does not describe pose estimation as the detector", () => {
    // README Key Concepts: ball tracking finds the play; pose refines the label.
    expect(host.textContent).toContain("Ball tracking");
    expect(host.textContent).not.toMatch(/pose estimation identifies/i);
  });

  it("shows no stat counters", () => {
    // The old StatCounter showed invented totals on a public page.
    expect(host.textContent).not.toMatch(/actions detected|clips generated|games analyzed/);
  });

  it("sets its own title and description", () => {
    expect(metadata.title).toContain("ClipFarm");
    expect(metadata.description).toBeTruthy();
    expect(metadata.openGraph?.images).toBeUndefined();
  });
});
