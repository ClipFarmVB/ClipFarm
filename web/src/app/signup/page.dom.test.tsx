// @vitest-environment jsdom
//
// CF-304: /signup's Google button is the same un-awaited `onClick` into
// `signInWithOAuth` as /login's, and failed the same way — a rejection from the
// PKCE code challenge gave no UI and an unhandled rejection. The reasoning for
// the assertions is in login/page.dom.test.tsx; this pins the same three cases
// on this page.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const signInWithOAuth = vi.hoisted(() => vi.fn());
const signUp = vi.hoisted(() => vi.fn());
vi.mock("@/lib/supabase", () => ({
  createClient: () => ({ auth: { signInWithOAuth, signUp } }),
}));
// A plain anchor: next/link's own behaviour is not under test here.
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import SignupPage from "@/app/signup/page";

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

async function mountAndClickGoogle(): Promise<{ text: () => string; unhandled: unknown[] }> {
  const unhandled: unknown[] = [];
  const onUnhandled = (e: unknown) => unhandled.push(e);
  process.on("unhandledRejection", onUnhandled);
  try {
    await act(async () => {
      root.render(<SignupPage />);
    });
    const button = [...host.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Continue with Google"),
    );
    if (!button) throw new Error("no Google button");
    await act(async () => {
      button.click();
    });
    // Two turns of the microtask queue, so a rejection that nothing handles has
    // been reported by the time the assertion runs.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
  return { text: () => host.textContent ?? "", unhandled };
}

describe("the signup Google button's failure path (CF-304)", () => {
  it("surfaces a rejection instead of failing silently", async () => {
    signInWithOAuth.mockRejectedValue(new Error("PKCE storage unavailable"));

    const { text, unhandled } = await mountAndClickGoogle();

    expect(text()).toContain("PKCE storage unavailable");
    expect(unhandled).toEqual([]);
    // Announced, not merely rendered, as on /login.
    const alert = host.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("PKCE storage unavailable");
  });

  it("falls back to generic copy when the rejection is not an Error", async () => {
    // `null` is the case that throws on `.message` inside the catch, which is
    // why the guard is `instanceof` rather than a property read.
    signInWithOAuth.mockRejectedValue(null);

    const { text, unhandled } = await mountAndClickGoogle();

    expect(text()).toContain("Please try again.");
    expect(unhandled).toEqual([]);
  });

  it("says nothing when the redirect is handed back without an error", async () => {
    // The other direction, so the fix cannot be "always show something": the
    // success case is a redirect, and a message on it would be a lie.
    signInWithOAuth.mockResolvedValue({
      data: { provider: "google", url: "https://example.test/authorize" },
      error: null,
    });

    const { text, unhandled } = await mountAndClickGoogle();

    expect(text()).not.toContain("Please try again.");
    expect(unhandled).toEqual([]);
  });
});
