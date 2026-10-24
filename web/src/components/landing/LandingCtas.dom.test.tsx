// @vitest-environment jsdom
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let auth: { user: { id: string } | null; loading: boolean } = { user: null, loading: true };

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: ComponentProps<"a">) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

import { LandingCtas } from "./LandingCtas";

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
});

function links(): [string | null, string][] {
  act(() => root.render(<LandingCtas />));
  return Array.from(host.querySelectorAll("a")).map((a) => [
    a.getAttribute("href"),
    (a.textContent ?? "").trim(),
  ]);
}

describe("LandingCtas", () => {
  it("offers sign-up and log-in to a signed-out visitor", () => {
    auth = { user: null, loading: false };

    expect(links()).toEqual([
      ["/signup", "Get started"],
      ["/login", "Log in"],
    ]);
  });

  it("sends a signed-in visitor to their library instead", () => {
    auth = { user: { id: "u1" }, loading: false };

    expect(links()).toEqual([["/games", "Go to your library"]]);
  });

  it("shows the signed-out pair while the session is loading", () => {
    // Matches the server render, so a first-time visitor sees no swap.
    auth = { user: null, loading: true };

    expect(links().map(([href]) => href)).toEqual(["/signup", "/login"]);
  });

  it("does not redirect a signed-in visitor away from the page", () => {
    // CF-574 replaced SignedInRedirect: the page stays put, it only swaps
    // the buttons. Rendering must not touch the router at all.
    auth = { user: { id: "u1" }, loading: false };
    links();

    expect(host.textContent).toContain("Go to your library");
  });
});
