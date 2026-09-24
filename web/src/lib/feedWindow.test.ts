/**
 * CF-112: the two rules that carry the card's acceptance criteria.
 *
 * "Scrolling 50+ posts doesn't leak video elements or degrade" is a claim about
 * how many indices `isLoaded` admits at once, and it is the one thing here that
 * cannot be caught by looking at the UI — a leak looks fine until the tab is
 * twenty posts deep on a phone. So it is asserted over a long feed rather than
 * described.
 */
import { describe, expect, it } from "vitest";

import {
  LOAD_RADIUS,
  PREFETCH_WITHIN,
  isLoaded,
  pickActiveIndex,
  shouldPrefetch,
} from "@/lib/feedWindow";

describe("isLoaded", () => {
  it("keeps the active post loaded", () => {
    expect(isLoaded(4, 4)).toBe(true);
  });

  it("preloads the next post so a swipe doesn't stall on a cold start", () => {
    expect(isLoaded(5, 4)).toBe(true);
  });

  it("keeps the previous post so scrolling back up is instant", () => {
    expect(isLoaded(3, 4)).toBe(true);
  });

  it("releases everything else", () => {
    expect(isLoaded(2, 4)).toBe(false);
    expect(isLoaded(6, 4)).toBe(false);
    expect(isLoaded(40, 4)).toBe(false);
  });

  it("admits a bounded number of posts however long the feed gets", () => {
    // The acceptance criterion, stated as arithmetic: the count is a function
    // of the radius, not of the feed length. If someone widens the window to
    // "fix" a stall, this is what tells them what it costs.
    for (const length of [50, 500, 5000]) {
      const active = Math.floor(length / 2);
      const loaded = Array.from({ length }, (_, i) => i).filter((i) => isLoaded(i, active));
      expect(loaded).toHaveLength(2 * LOAD_RADIUS + 1);
    }
  });

  it("never admits more than the window at the ends of the feed either", () => {
    const length = 50;
    for (const active of [0, 1, length - 2, length - 1]) {
      const loaded = Array.from({ length }, (_, i) => i).filter((i) => isLoaded(i, active));
      expect(loaded.length).toBeLessThanOrEqual(2 * LOAD_RADIUS + 1);
    }
  });

  it("holds three at most across a whole 50-post scroll", () => {
    // Walks the feed the way a user does, rather than sampling one position.
    let worst = 0;
    for (let active = 0; active < 50; active++) {
      const n = Array.from({ length: 50 }, (_, i) => i).filter((i) => isLoaded(i, active)).length;
      worst = Math.max(worst, n);
    }
    expect(worst).toBe(2 * LOAD_RADIUS + 1);
  });
});

describe("shouldPrefetch", () => {
  it("waits while there is plenty ahead", () => {
    expect(shouldPrefetch(0, 20)).toBe(false);
    expect(shouldPrefetch(16, 20)).toBe(false);
  });

  it("fires with a few posts left, not at the very end", () => {
    // Fetching at the last post means the user hits a wall while the request is
    // in flight, which on a snap container reads as the feed having ended.
    expect(shouldPrefetch(20 - PREFETCH_WITHIN, 20)).toBe(true);
    expect(shouldPrefetch(19, 20)).toBe(true);
  });

  it("fires on an empty list so the first page is requested", () => {
    expect(shouldPrefetch(0, 0)).toBe(true);
  });

  it("stays true past the end rather than going quiet", () => {
    // A stale activeIndex after posts are filtered out must not strand the
    // feed with nothing loaded and no pending request.
    expect(shouldPrefetch(25, 20)).toBe(true);
  });
});

describe("pickActiveIndex", () => {
  const entry = (index: string | undefined, ratio: number, isIntersecting = ratio > 0) => ({
    isIntersecting,
    intersectionRatio: ratio,
    target: { dataset: index === undefined ? {} : { index } } as unknown as Element,
  });

  it("picks the only intersecting post at a settled snap point", () => {
    expect(pickActiveIndex([entry("3", 1), entry("4", 0, false)])).toBe(3);
  });

  it("picks the most visible one mid-swipe", () => {
    // The case that makes this worth extracting: two entries clear the
    // threshold while the snap settles, and "last one wins" would hand the
    // active slot to whichever the browser happened to list second.
    expect(pickActiveIndex([entry("3", 0.62), entry("4", 0.95)])).toBe(4);
    expect(pickActiveIndex([entry("4", 0.95), entry("3", 0.62)])).toBe(4);
  });

  it("ignores entries that are leaving", () => {
    expect(pickActiveIndex([entry("2", 0, false), entry("3", 0.8)])).toBe(3);
  });

  it("keeps the current post when nothing qualifies", () => {
    // Returning 0 here would snap playback back to the top of the feed every
    // time a batch contained only exits.
    expect(pickActiveIndex([entry("2", 0, false)])).toBeNull();
    expect(pickActiveIndex([])).toBeNull();
  });

  it("survives a target without a usable index", () => {
    expect(pickActiveIndex([entry(undefined, 0.9)])).toBeNull();
    expect(pickActiveIndex([entry("not-a-number", 0.9)])).toBeNull();
  });

  it("treats index 0 as a real answer, not a falsy one", () => {
    expect(pickActiveIndex([entry("0", 1)])).toBe(0);
  });
});
