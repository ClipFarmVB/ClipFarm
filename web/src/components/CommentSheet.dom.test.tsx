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
