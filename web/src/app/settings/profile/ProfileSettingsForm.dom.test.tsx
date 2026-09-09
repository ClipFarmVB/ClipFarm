// @vitest-environment jsdom
//
// CF-109b item 3 (#398): what the privacy switch claims to do.
//
// This is a copy test, which is unusual, and it earns its place because the
// copy was making a false safety claim on youth-sports footage. `is_private`
// governs one thing — whether following needs approval — and the switch used
// to describe itself as the control over who can see your clips. It is not:
// `services/access.py` argues that at length and
// `test_account_privacy_does_not_clamp_post_visibility` pins it on the api
// side, so a private account's `public` post stays readable by a signed-out
// stranger.
//
// The failure that invites is specific and unrecoverable: publish clips
// publicly, later flip to Private believing that retracted them, and it did
// not. So the claim is pinned here rather than left to the next person
// editing a settings string.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProfileSettingsForm } from "./ProfileSettingsForm";

const getMe = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  getMe,
  updateMe: vi.fn(),
  uploadAvatar: vi.fn(),
  checkHandle: vi.fn(),
}));
// The form renders inside RequireAuth, which is a session gate, not the
// subject here.
vi.mock("@/components/RequireAuth", () => ({
  RequireAuth: ({ children }: { children: React.ReactNode }) => children,
}));

let container: HTMLDivElement;
let root: Root;

async function mount(isPrivate: boolean) {
  getMe.mockResolvedValue({
    id: "user-1",
    email: "a@example.com",
    username: "alice",
    display_name: "Alice",
    bio: "",
    avatar_url: null,
    is_private: isPrivate,
    username_changed_at: null,
    username_is_generated: false,
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<ProfileSettingsForm />);
  });
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

function text(): string {
  return container.textContent ?? "";
}

function toggle() {
  const button = [...container.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes("account"),
  )!;
  return act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("what the privacy switch says it does", () => {
  it("describes Private as an approval gate on followers", async () => {
    await mount(true);
    expect(text()).toContain("Private account");
    expect(text()).toContain("approval");
  });

  it("describes Public as open following", async () => {
    await mount(false);
    expect(text()).toContain("Public account");
    expect(text()).toContain("follow you without asking");
  });

  it("never claims the switch controls who can see a clip, in either state", async () => {
    // The two sentences this replaces, verbatim. Asserting their absence is
    // the point: a future edit that reintroduces either phrasing is making the
    // same false claim again, whatever else it changes.
    const forbidden = [
      "Only followers you approve can see your clips",
      "Anyone can see your clips",
    ];
    await mount(true);
    for (const phrase of forbidden) expect(text()).not.toContain(phrase);

    await toggle();
    expect(text()).toContain("Public account");
    for (const phrase of forbidden) expect(text()).not.toContain(phrase);
  });

  it("says where clip visibility is actually chosen, in either state", async () => {
    await mount(true);
    expect(text()).toContain("This controls follows, not footage");
    expect(text()).toContain("chosen when");

    await toggle();
    // The note sits outside the switch, so it must survive the toggle — a
    // reader who flips to Public is exactly the one about to assume it
    // published something.
    expect(text()).toContain("This controls follows, not footage");
  });

  it("warns that switching to Private does not retract a public post", async () => {
    await mount(false);
    expect(text()).toContain("stays public");
  });
});
