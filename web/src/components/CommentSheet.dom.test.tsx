// @vitest-environment jsdom
//
// CF-113: the parts of the comment sheet where a wrong call is invisible in
// review and obvious to a user — the delete control appearing for someone who
// cannot delete (a button that only ever 404s), a posted comment that does not
// appear until a refetch, and a count on the card that drifts from the list.
//
// `createRoot` + `act` rather than a testing library, matching
// `FeedPost.dom.test.tsx` and `PostComposerModal.dom.test.tsx`.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommentSheet } from "./CommentSheet";
import type { Comment, Me, Post } from "@/lib/api";

const getComments = vi.hoisted(() => vi.fn());
const createComment = vi.hoisted(() => vi.fn());
const deleteComment = vi.hoisted(() => vi.fn());
const meRef = vi.hoisted(() => ({ current: null as Me | null }));

vi.mock("@/lib/api", () => ({ getComments, createComment, deleteComment }));
vi.mock("@/lib/useMe", () => ({ useMe: () => meRef.current }));
// The sheet gates `useMe` on the session, matching every other caller. Neither
// the session nor the feature flag is the subject here.
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "session" }, loading: false }),
}));
vi.mock("@/lib/features", () => ({ SOCIAL_ENABLED: true }));

const AUTHOR = {
  id: "user-1",
  username: "alice",
  display_name: "Alice",
  avatar_url: null,
};
const OTHER = {
  id: "user-2",
  username: "bob",
  display_name: "Bob",
  avatar_url: null,
};

function makePost(): Post {
  return { id: "post-1", author: AUTHOR, comment_count: 2 } as Post;
}

function makeComment(id: string, author = OTHER, body = "nice"): Comment {
  return {
    id,
    post_id: "post-1",
    body,
    created_at: "2026-08-01T12:00:00+00:00",
    author,
  };
}

function asMe(profile: typeof AUTHOR): Me {
  return { ...profile, email: "x@example.com", username_changed_at: null, username_is_generated: false } as Me;
}

let container: HTMLDivElement;
let root: Root;

async function mount(onClose = () => {}, onCountChange?: (d: number) => void) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <CommentSheet post={makePost()} onClose={onClose} onCountChange={onCountChange} />,
    );
  });
}

beforeEach(() => {
  meRef.current = null;
  getComments.mockResolvedValue({ items: [], next_cursor: null });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

// The sheet portals into document.body, so queries go through the document
// rather than the render container.
function sheet(): HTMLElement {
  return document.querySelector('[role="dialog"]') as HTMLElement;
}
function deleteButtons(): HTMLButtonElement[] {
  return [...sheet().querySelectorAll("button")].filter(
    (b) => b.getAttribute("aria-label") === "Delete comment",
  ) as HTMLButtonElement[];
}
function postButton(): HTMLButtonElement {
  return [...sheet().querySelectorAll("button")].find(
    (b) => (b.textContent ?? "").startsWith("Post"),
  ) as HTMLButtonElement;
}
function textarea(): HTMLTextAreaElement {
  return sheet().querySelector("textarea") as HTMLTextAreaElement;
}
async function type(value: string) {
  const el = textarea();
  await act(async () => {
    // React tracks the last value it wrote on the node; setting `.value`
    // directly makes its change detection skip the event as a no-op.
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function click(el: Element) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("loading", () => {
  it("asks for the first page on mount and renders it", async () => {
    getComments.mockResolvedValue({
      items: [makeComment("c1", OTHER, "great dig"), makeComment("c2", AUTHOR, "thanks")],
      next_cursor: null,
    });
    await mount();

    expect(getComments).toHaveBeenCalledWith("post-1", null);
    expect(sheet().textContent).toContain("great dig");
    expect(sheet().textContent).toContain("thanks");
  });

  it("pages on the cursor the server handed back, not an offset", async () => {
    getComments.mockResolvedValueOnce({ items: [makeComment("c1")], next_cursor: "cur-1" });
    await mount();

    getComments.mockResolvedValueOnce({
      items: [makeComment("c2", OTHER, "second page")],
      next_cursor: null,
    });
    const more = [...sheet().querySelectorAll("button")].find(
      (b) => b.textContent === "Load more",
    )!;
    await click(more);

    expect(getComments).toHaveBeenLastCalledWith("post-1", "cur-1");
    expect(sheet().textContent).toContain("second page");
    // Last page: the control goes away rather than paging past the end.
    expect(
      [...sheet().querySelectorAll("button")].some((b) => b.textContent === "Load more"),
    ).toBe(false);
  });
});

describe("posting", () => {
  it("cannot submit an empty or whitespace-only body", async () => {
    await mount();
    expect(postButton().disabled).toBe(true);

    await type("   ");
    expect(postButton().disabled).toBe(true);

    await type("ok");
    expect(postButton().disabled).toBe(false);
  });

  it("prepends the new comment and tells the card to count it", async () => {
    getComments.mockResolvedValue({ items: [makeComment("c1", OTHER, "older")], next_cursor: null });
    createComment.mockResolvedValue(makeComment("c9", OTHER, "brand new"));
    const onCountChange = vi.fn();
    await mount(() => {}, onCountChange);

    await type("  brand new  ");
    await click(postButton());

    expect(createComment).toHaveBeenCalledWith("post-1", "brand new");
    const bodies = [...sheet().querySelectorAll("li[data-comment-id]")].map(
      (li) => li.getAttribute("data-comment-id"),
    );
    expect(bodies).toEqual(["c9", "c1"]);
    expect(onCountChange).toHaveBeenCalledWith(1);
    expect(textarea().value).toBe("");
  });

  it("keeps the draft and does not count a comment the server refused", async () => {
    createComment.mockRejectedValue(new Error("Comment is too long."));
    const onCountChange = vi.fn();
    await mount(() => {}, onCountChange);

    await type("rejected");
    await click(postButton());

    expect(onCountChange).not.toHaveBeenCalled();
    expect(sheet().textContent).toContain("Comment is too long.");
    expect(textarea().value).toBe("rejected");
  });
});

describe("deleting", () => {
  // The API lets the comment's author or the post's author delete. Offering
  // the control to anyone else is a button that can only 404, so the rule is
  // mirrored here rather than left to the response.
  it("offers no delete control to a signed-out reader", async () => {
    getComments.mockResolvedValue({
      items: [makeComment("c1", OTHER), makeComment("c2", AUTHOR)],
      next_cursor: null,
    });
    meRef.current = null;
    await mount();

    expect(deleteButtons()).toHaveLength(0);
  });

  it("offers it on the reader's own comment only", async () => {
    getComments.mockResolvedValue({
      items: [makeComment("c1", OTHER), makeComment("c2", { ...OTHER, id: "user-3", username: "carol", display_name: "Carol" })],
      next_cursor: null,
    });
    // Bob is neither the post's author (Alice) nor the author of c2.
    meRef.current = asMe(OTHER);
    await mount();

    expect(deleteButtons()).toHaveLength(1);
    expect(deleteButtons()[0].closest("li")?.getAttribute("data-comment-id")).toBe("c1");
  });

  it("offers it on every comment to the post's author", async () => {
    getComments.mockResolvedValue({
      items: [makeComment("c1", OTHER), makeComment("c2", AUTHOR)],
      next_cursor: null,
    });
    meRef.current = asMe(AUTHOR); // Alice owns post-1.
    await mount();

    expect(deleteButtons()).toHaveLength(2);
  });

  it("drops the row and decrements the card's count", async () => {
    getComments.mockResolvedValue({ items: [makeComment("c1", OTHER)], next_cursor: null });
    deleteComment.mockResolvedValue(undefined);
    meRef.current = asMe(AUTHOR);
    const onCountChange = vi.fn();
    await mount(() => {}, onCountChange);

    await click(deleteButtons()[0]);

    expect(deleteComment).toHaveBeenCalledWith("c1");
    expect(sheet().querySelectorAll("li[data-comment-id]")).toHaveLength(0);
    expect(onCountChange).toHaveBeenCalledWith(-1);
  });

  it("leaves the row in place when the delete fails", async () => {
    getComments.mockResolvedValue({ items: [makeComment("c1", OTHER)], next_cursor: null });
    deleteComment.mockRejectedValue(new Error("Not yours."));
    meRef.current = asMe(AUTHOR);
    const onCountChange = vi.fn();
    await mount(() => {}, onCountChange);

    await click(deleteButtons()[0]);

    expect(sheet().querySelectorAll("li[data-comment-id]")).toHaveLength(1);
    expect(onCountChange).not.toHaveBeenCalled();
  });
});

describe("dismissal", () => {
  it("closes on Escape", async () => {
    const onClose = vi.fn();
    await mount(onClose);

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on the overlay but not on the card itself", async () => {
    const onClose = vi.fn();
    await mount(onClose);

    await click(sheet());
    expect(onClose).not.toHaveBeenCalled();

    await click(sheet().parentElement as Element);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// ── gaps a review round found: each of these was green against a wrong version ──

function rowIds(): (string | null)[] {
  return [...sheet().querySelectorAll("li[data-comment-id]")].map((li) =>
    li.getAttribute("data-comment-id"),
  );
}

describe("paging keeps what it already has", () => {
  it("appends the next page rather than replacing the list", async () => {
    // The paging test above asserted the cursor, that page 2 appeared, and that
    // "Load more" went away — and stayed green when the list was REPLACED on
    // every page, which is the single most likely way to get this wrong.
    getComments.mockResolvedValueOnce({ items: [makeComment("c1")], next_cursor: "cur-1" });
    await mount();

    getComments.mockResolvedValueOnce({ items: [makeComment("c2")], next_cursor: null });
    await click(
      [...sheet().querySelectorAll("button")].find((b) => b.textContent === "Load more")!,
    );

    expect(rowIds()).toEqual(["c1", "c2"]);
  });

  it("shows a comment once when two pages overlap", async () => {
    // The server's keyset cursor is stable, but a comment deleted between the
    // two requests shifts the window and can repeat a row. The dedupe filter
    // handles it and nothing exercised it.
    getComments.mockResolvedValueOnce({ items: [makeComment("c1")], next_cursor: "cur-1" });
    await mount();

    getComments.mockResolvedValueOnce({
      items: [makeComment("c1"), makeComment("c2")],
      next_cursor: null,
    });
    await click(
      [...sheet().querySelectorAll("button")].find((b) => b.textContent === "Load more")!,
    );

    expect(rowIds()).toEqual(["c1", "c2"]);
  });
});

describe("the textarea's own submit path", () => {
  it("sends on Enter", async () => {
    // Enter-to-submit was not exercised at all, so neither this nor the
    // re-entrancy guard below had any coverage.
    createComment.mockResolvedValue(makeComment("c9", OTHER, "typed"));
    await mount();
    await type("typed");

    await act(async () => {
      textarea().dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }),
      );
    });

    expect(createComment).toHaveBeenCalledWith("post-1", "typed");
  });

  it("keeps a newline on Shift+Enter instead of sending", async () => {
    await mount();
    await type("first line");

    await act(async () => {
      textarea().dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Enter",
          shiftKey: true,
          bubbles: true,
          cancelable: true,
        }),
      );
    });

    expect(createComment).not.toHaveBeenCalled();
  });

  it("does not send twice when Enter is held down", async () => {
    // `canSubmit` is gated on `!saving`; without it a repeat keydown posts the
    // same comment again while the first is in flight.
    let release: (v: unknown) => void = () => {};
    createComment.mockReturnValue(new Promise((r) => (release = r)));
    await mount();
    await type("held");

    for (let i = 0; i < 3; i++) {
      await act(async () => {
        textarea().dispatchEvent(
          new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }),
        );
      });
    }
    await act(async () => release(makeComment("c9", OTHER, "held")));

    expect(createComment).toHaveBeenCalledTimes(1);
  });
});

describe("deleting is serialised too", () => {
  it("sends one DELETE for a double tap, and counts it once", async () => {
    // The trash icon is a 22px target on a phone, which is where a double tap
    // is most likely. Two DELETEs either draw a "not found" banner for a
    // deletion that worked, or take the card's count down by two for one
    // comment.
    getComments.mockResolvedValue({ items: [makeComment("c1", OTHER)], next_cursor: null });
    let release: () => void = () => {};
    deleteComment.mockReturnValue(new Promise<void>((r) => (release = r)));
    meRef.current = asMe(AUTHOR);
    const onCountChange = vi.fn();
    await mount(() => {}, onCountChange);

    const button = deleteButtons()[0];
    await click(button);
    await click(button);
    await act(async () => release());

    expect(deleteComment).toHaveBeenCalledTimes(1);
    expect(onCountChange).toHaveBeenCalledTimes(1);
  });

  it("shows the delete controls again once the delete settles", async () => {
    // The guard is two belts — a busy ref in `remove` and `disabled` on the
    // button — so removing either alone still sends one DELETE and the test
    // above cannot tell them apart. This is what makes the disabled state
    // itself observable: a version that never re-enables leaves the sheet
    // permanently unable to delete anything after the first attempt.
    getComments.mockResolvedValue({
      items: [makeComment("c1", OTHER), makeComment("c2", OTHER)],
      next_cursor: null,
    });
    let release: () => void = () => {};
    deleteComment.mockReturnValue(new Promise<void>((r) => (release = r)));
    meRef.current = asMe(AUTHOR);
    await mount();

    await click(deleteButtons()[0]);
    expect(deleteButtons().every((b) => b.disabled)).toBe(true);

    await act(async () => release());
    expect(deleteButtons().some((b) => !b.disabled)).toBe(true);
  });
});

describe("being handed a different post", () => {
  it("drops the old post's comments and cursor rather than stapling to them", async () => {
    // `load` appends and `cursor` is not otherwise cleared, so without the
    // reset a change of post would show the new post's comments under the old
    // post's and page on with the old post's cursor. Unreachable while the
    // feed keys cards by id — which is why nothing would report it the day
    // that stops being true.
    getComments.mockResolvedValueOnce({
      items: [makeComment("c1", OTHER, "belongs to post 1")],
      next_cursor: "cur-old",
    });
    await mount();
    expect(rowIds()).toEqual(["c1"]);

    getComments.mockResolvedValueOnce({
      items: [makeComment("c9", OTHER, "belongs to post 2")],
      next_cursor: null,
    });
    await act(async () => {
      root.render(
        <CommentSheet
          post={{ ...makePost(), id: "post-2" } as Post}
          onClose={() => {}}
        />,
      );
    });

    expect(getComments).toHaveBeenLastCalledWith("post-2", null);
    expect(rowIds()).toEqual(["c9"]);
    expect(sheet().textContent).not.toContain("belongs to post 1");
    // ...and the old cursor is gone with it.
    expect(
      [...sheet().querySelectorAll("button")].some((b) => b.textContent === "Load more"),
    ).toBe(false);
  });
});

describe("what the sheet tells assistive tech", () => {
  it("moves focus into the textarea on open", async () => {
    // Correct, and pinned by nothing — the Escape test only kills a wholly
    // disabled trap.
    await mount();
    expect(document.activeElement).toBe(textarea());
  });

  it("announces a failure rather than only showing it", async () => {
    // In a modal this banner is the only feedback, and focus stays in the
    // textarea, so without a live region a screen-reader user gets silence.
    createComment.mockRejectedValue(new Error("Comment is too long."));
    await mount();
    await type("nope");
    await click(postButton());

    const alert = sheet().querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain("Comment is too long.");
  });
});
