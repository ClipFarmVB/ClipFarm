// @vitest-environment jsdom
//
// CF-109b (#398): what the composer asks for before it widens a clip.
//
// The unit test beside this one covers the two rules as arithmetic
// (`tierNeedsRaise`, `tierUnavailable`). This covers the part that is only
// visible through the component: that picking a wider tier is NOT on its own
// consent to change the footage, that the consent does not survive changing
// your mind, and that the flag reaches `createPost` rather than being a
// checkbox that does nothing.
//
// `createRoot` + `act`, matching the sibling dom suites.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const createPost = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ createPost }));

import { PostComposerModal } from "@/components/PostComposerModal";
import type { Clip, Visibility } from "@/lib/api";

function makeClip(ceiling: Visibility | null): Clip {
  return {
    id: "clip-1",
    game_id: "game-1",
    action_type: "spike",
    confidence: 0.9,
    start_time: 10,
    end_time: 18,
    clip_url: "https://example.test/c.mp4",
    thumbnail_url: null,
    labels: [],
    effective_visibility: ceiling,
  } as unknown as Clip;
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  createPost.mockResolvedValue({});
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
});

function mount(ceiling: Visibility | null) {
  act(() => {
    root.render(<PostComposerModal clip={makeClip(ceiling)} onClose={() => {}} />);
  });
}

function card(): HTMLElement {
  return document.querySelector('[role="dialog"]') as HTMLElement;
}
function tier(label: string): HTMLButtonElement {
  return [...card().querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").includes(label),
  ) as HTMLButtonElement;
}
function postButton(): HTMLButtonElement {
  return [...card().querySelectorAll("button")].find(
    (b) => (b.textContent ?? "").trim() === "Post",
  ) as HTMLButtonElement;
}
function consentBox(): HTMLInputElement | null {
  return card().querySelector('input[type="checkbox"]');
}
async function tick() {
  // React tracks the last value it wrote to the node, so assigning `.checked`
  // directly makes its change detection treat the event as a no-op. Going
  // through the prototype setter is what makes this a real user tick.
  const box = consentBox()!;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "checked",
    )!.set!;
    setter.call(box, true);
    box.dispatchEvent(new Event("click", { bubbles: true }));
  });
}

async function click(el: Element) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("posting within the clip's own tier", () => {
  it("asks for nothing and does not raise", async () => {
    mount("followers");

    await click(tier("Followers"));
    expect(consentBox()).toBeNull();
    expect(postButton().disabled).toBe(false);

    await click(postButton());
    expect(createPost).toHaveBeenCalledWith("clip-1", "", "followers", false);
  });
});

describe("posting wider than the clip", () => {
  it("offers the tier rather than greying it out", () => {
    // The change CF-109b makes. Before it, this option was disabled with a
    // reason and the user had no way to act on it.
    mount("private");
    expect(tier("Followers").disabled).toBe(false);
  });

  it("will not post until the consequence is acknowledged", async () => {
    mount("private");
    await click(tier("Followers"));

    const box = consentBox();
    expect(box).not.toBeNull();
    // Selecting the tier is not consent: this changes what people can see of
    // the CLIP, which outlives the post and is not undone by deleting it.
    expect(box!.checked).toBe(false);
    expect(postButton().disabled).toBe(true);
    expect(createPost).not.toHaveBeenCalled();
  });

  it("says what will change, in terms of the footage rather than the post", async () => {
    mount("private");
    await click(tier("Followers"));

    const text = card().textContent ?? "";
    expect(text).toContain("this clip is private");
    expect(text).toContain("will also change the clip itself");
    // The half a user would otherwise discover later: deleting the post does
    // not put the clip back.
    expect(text).toContain("after this post is deleted");
    // ...and where the undo is. This sentence was removed when the API allowed
    // narrowing and no control did — a promise the user would go looking for
    // and not find. The control exists now, so it names where.
    expect(text).toContain("make it private again from the clip");
  });

  it("sends the raise once acknowledged", async () => {
    mount("private");
    await click(tier("Followers"));
    await tick();

    expect(postButton().disabled).toBe(false);
    await click(postButton());
    expect(createPost).toHaveBeenCalledWith("clip-1", "", "followers", true);
  });

  it("forgets the acknowledgement when the tier changes", async () => {
    // A tick given for "Followers" is not a tick for "Only me" and back again,
    // and it is certainly not one for a wider tier. Carrying it would widen
    // the clip further than the user agreed to, without asking again.
    mount("private");
    await click(tier("Followers"));
    await tick();
    expect(postButton().disabled).toBe(false);

    await click(tier("Only me"));
    await click(tier("Followers"));

    expect(consentBox()!.checked).toBe(false);
    expect(postButton().disabled).toBe(true);
  });
});

describe("a tier this deployment does not offer", () => {
  it("stays disabled with a reason, because no consent makes it available", () => {
    // PUBLIC_POSTING_ENABLED is off in the test environment, which is the
    // shipped default. The ceiling is `public` here, so nothing else could be
    // disabling this option.
    mount("public");

    const everyone = tier("Everyone");
    expect(everyone.disabled).toBe(true);
    expect(everyone.textContent).toContain("Not available on this app yet");
    // ...and the tier below it is unaffected. Folding the two rules together
    // would grey out Followers on a private clip again.
    expect(tier("Followers").disabled).toBe(false);
  });
});

describe("when the server refuses anyway", () => {
  it("surfaces the reason rather than a generic failure", async () => {
    // The backstop for a clip narrowed between page load and click, and for
    // the two PUBLIC_POSTING_ENABLED flags disagreeing.
    createPost.mockRejectedValue(new Error("This clip is private, so it can only…"));
    mount("followers");

    await click(tier("Followers"));
    await click(postButton());

    expect(card().textContent).toContain("This clip is private, so it can only…");
  });
});

describe("gaps a review round found", () => {
  it("asks for consent when the clip's tier is unreadable, not when it is not", async () => {
    // The component's own `?? "private"` fail-closed default. The unit test
    // pins the helper for `undefined`; nothing pinned the coalesce here, which
    // also feeds the sentence the user is consenting to.
    mount(null);

    await click(tier("Followers"));
    expect(consentBox()).not.toBeNull();
    expect(card().textContent).toContain("this clip is private");
  });

  it("re-disables Post when the tick is taken back", async () => {
    // Every existing assertion ticks. A checkbox that cannot be unticked reads
    // as consent the user is not allowed to withdraw.
    mount("private");
    await click(tier("Followers"));
    await tick();
    expect(postButton().disabled).toBe(false);

    await act(async () => {
      const box = consentBox()!;
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "checked",
      )!.set!;
      setter.call(box, false);
      box.dispatchEvent(new Event("click", { bubbles: true }));
    });

    expect(postButton().disabled).toBe(true);
  });

  it("points the disabled Post button at the reason it is disabled", async () => {
    // A disabled button is out of the tab order and explains nothing. The
    // blocked tier already had this wiring; the button did not.
    mount("private");
    await click(tier("Followers"));

    const described = postButton().getAttribute("aria-describedby");
    expect(described).toBeTruthy();
    expect(card().querySelector(`#${described}`)).not.toBeNull();
    expect(card().querySelector(`#${described}`)?.textContent).toContain(
      "will also change the clip itself",
    );
  });

  it("drops the pointer once the button is usable", async () => {
    mount("private");
    await click(tier("Followers"));
    await tick();
    expect(postButton().getAttribute("aria-describedby")).toBeNull();
  });

  it("announces a refusal rather than only showing it", async () => {
    createPost.mockRejectedValue(new Error("This clip is private, so it can only…"));
    mount("followers");
    await click(tier("Followers"));
    await click(postButton());

    const alert = card().querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain("This clip is private");
  });
});
