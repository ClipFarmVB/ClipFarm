import { describe, expect, it } from "vitest";

import { createClassLock, type ClassTarget } from "./classLock";

function stubTarget() {
  const classes = new Set<string>();
  const target: ClassTarget = {
    add: (c) => { classes.add(c); },
    remove: (c) => { classes.delete(c); },
  };
  return { classes, target: () => target };
}

describe("createClassLock", () => {
  it("adds the class on the first acquire only", () => {
    const { classes, target } = stubTarget();
    const lock = createClassLock(target);

    lock.acquire("lock");
    lock.acquire("lock");

    expect(classes.has("lock")).toBe(true);
    expect(lock.held("lock")).toBe(2);
  });

  // The reason the hook exists: the collection picker opening over the clip
  // modal must not unlock the page when the picker alone closes.
  it("keeps the class until the last holder releases", () => {
    const { classes, target } = stubTarget();
    const lock = createClassLock(target);

    lock.acquire("lock");
    lock.acquire("lock");

    lock.release("lock");
    expect(classes.has("lock")).toBe(true);
    expect(lock.held("lock")).toBe(1);

    lock.release("lock");
    expect(classes.has("lock")).toBe(false);
    expect(lock.held("lock")).toBe(0);
  });

  it("counts each class separately", () => {
    const { classes, target } = stubTarget();
    const lock = createClassLock(target);

    lock.acquire("a");
    lock.acquire("b");
    lock.release("a");

    expect(classes.has("a")).toBe(false);
    expect(classes.has("b")).toBe(true);
  });

  // React re-runs effects in development (mount → cleanup → mount). The count
  // has to come back to exactly 1, not 2.
  it("survives a mount/cleanup/mount cycle", () => {
    const { classes, target } = stubTarget();
    const lock = createClassLock(target);

    lock.acquire("lock");
    lock.release("lock");
    lock.acquire("lock");

    expect(classes.has("lock")).toBe(true);
    expect(lock.held("lock")).toBe(1);
  });

  it("fails safe on an unbalanced release rather than wedging the class on", () => {
    const { classes, target } = stubTarget();
    const lock = createClassLock(target);

    lock.release("never-acquired");
    expect(classes.has("never-acquired")).toBe(false);
    expect(lock.held("never-acquired")).toBe(0);

    // A stray release must not push the count negative, which would make the
    // next acquire/release pair leave the page locked forever.
    lock.acquire("lock");
    lock.release("lock");
    lock.release("lock");
    lock.acquire("lock");
    lock.release("lock");
    expect(classes.has("lock")).toBe(false);
  });

  it("runs the pin/restore hooks once each, not per holder", () => {
    const { target } = stubTarget();
    const lock = createClassLock(target);
    let pinned = 0;
    let restored = 0;
    const hooks = {
      onFirstAcquire: () => { pinned += 1; },
      onLastRelease: () => { restored += 1; },
    };

    lock.acquire("lock", hooks);
    lock.acquire("lock", hooks);
    lock.release("lock", hooks);
    expect([pinned, restored]).toEqual([1, 0]);

    lock.release("lock", hooks);
    expect([pinned, restored]).toEqual([1, 1]);
  });

  it("pins before the class lands so the scroll offset is read unlocked", () => {
    const seen: string[] = [];
    const classes = new Set<string>();
    const lock = createClassLock(() => ({
      add: (c) => { seen.push("add"); classes.add(c); },
      remove: (c) => { seen.push("remove"); classes.delete(c); },
    }));

    lock.acquire("lock", { onFirstAcquire: () => seen.push("pin") });
    lock.release("lock", { onLastRelease: () => seen.push("restore") });

    expect(seen).toEqual(["pin", "add", "remove", "restore"]);
  });
});
