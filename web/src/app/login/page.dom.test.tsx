// @vitest-environment jsdom
//
// CF-304: the Google button had no test file at all, which is how a dead error
// arm shipped past six rounds of review.
//
// `signInWithOAuth` fails two ways and only one of them is a return value. In
// auth-js 2.100.0 `_handleProviderSignIn` has a single exit,
// `{ data: { provider, url }, error: null }`, so the `{ error }` the handler
// reads is the API's shape rather than a path this call reaches. The reachable
// failure is a REJECTION — `_getUrlForProvider` awaits the PKCE code challenge
// (storage, `crypto.subtle`) and catches nothing — and the handler is an
// un-awaited `onClick`, so before the `try/catch` it surfaced as an unhandled
// rejection and no UI whatsoever.
//
// So the assertion that matters is the throwing case, and the unhandled-
// rejection check beside it: a version of the handler that renders the message
// but leaves the rejection unhandled passes the first assertion alone.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const signInWithOAuth = vi.hoisted(() => vi.fn());
const signInWithPassword = vi.hoisted(() => vi.fn());
vi.mock("@/lib/supabase", () => ({
  createClient: () => ({ auth: { signInWithOAuth, signInWithPassword } }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
// A plain anchor: next/link's own behaviour is not under test here.
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import LoginPage from "@/app/login/page";

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
      root.render(<LoginPage />);
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

describe("the Google button's failure path (CF-304)", () => {
  it("surfaces a rejection instead of failing silently", async () => {
    signInWithOAuth.mockRejectedValue(new Error("PKCE storage unavailable"));

    const { text, unhandled } = await mountAndClickGoogle();

    expect(text()).toContain("PKCE storage unavailable");
    expect(unhandled).toEqual([]);
    // Announced, not merely rendered: focus stays on the Google button, so
    // without the role a screen-reader user hears nothing. Same idiom as
    // ClipModal and PostComposerModal.
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
