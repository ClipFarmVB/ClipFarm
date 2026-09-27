// CF-221: the onboarding record and the progress it is derived from.
//
// The record module keeps a module-level memory fallback, so each case uses its
// own user id rather than relying on a reset between cases.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Game } from "@/lib/api";
import { onboardingKey, onboardingProgress, readOnboarding, writeOnboarding } from "@/lib/onboarding";

function game(id: string, status: Game["status"]): Game {
  return { id, title: id, status, progress: 0, progress_stage: null, created_at: "2026-01-01T00:00:00Z" };
}

function fakeStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() { return data.size; },
    clear: () => data.clear(),
    getItem: (k: string) => data.get(k) ?? null,
    key: (i: number) => Array.from(data.keys())[i] ?? null,
    removeItem: (k: string) => { data.delete(k); },
    setItem: (k: string, v: string) => { data.set(k, v); },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("onboarding record", () => {
  it("keys the record per user", () => {
    expect(onboardingKey("a")).not.toBe(onboardingKey("b"));
    expect(onboardingKey("a")).toBe("cf-onboarding:a");
  });

  it("reads a stored record and ignores junk", () => {
    const s = fakeStorage();
    vi.stubGlobal("window", { localStorage: s });
    s.setItem(onboardingKey("read-1"), "skipped");
    s.setItem(onboardingKey("read-2"), "maybe");

    expect(readOnboarding("read-1")).toBe("skipped");
    expect(readOnboarding("read-2")).toBeNull();
    expect(readOnboarding("read-3")).toBeNull();
  });

  it("writes to storage under the user's key only", () => {
    const s = fakeStorage();
    vi.stubGlobal("window", { localStorage: s });

    writeOnboarding("write-1", "done");

    expect(s.getItem(onboardingKey("write-1"))).toBe("done");
    expect(s.length).toBe(1);
    expect(readOnboarding("write-2")).toBeNull();
  });

  it("falls back to memory when storage throws", () => {
    const throwing = fakeStorage();
    throwing.getItem = () => { throw new Error("blocked"); };
    throwing.setItem = () => { throw new Error("blocked"); };
    vi.stubGlobal("window", { localStorage: throwing });

    expect(readOnboarding("throw-1")).toBeNull();
    expect(() => writeOnboarding("throw-1", "skipped")).not.toThrow();
    expect(readOnboarding("throw-1")).toBe("skipped");
  });

  it("falls back to memory when the localStorage accessor itself throws", () => {
    // Safari with site data blocked throws on the property access.
    vi.stubGlobal("window", {
      get localStorage(): Storage { throw new Error("SecurityError"); },
    });

    expect(readOnboarding("accessor-1")).toBeNull();
    writeOnboarding("accessor-1", "skipped");
    expect(readOnboarding("accessor-1")).toBe("skipped");
  });

  it("works with no window at all", () => {
    expect(typeof window).toBe("undefined");
    writeOnboarding("nowindow-1", "done");
    expect(readOnboarding("nowindow-1")).toBe("done");
  });
});

describe("onboardingProgress", () => {
  it("is empty with no games", () => {
    expect(onboardingProgress([], null)).toEqual({ uploaded: false, inProgressGameId: null, ready: false });
  });

  it("reports the in-progress game", () => {
    const p = onboardingProgress([game("g1", "processing")], null);
    expect(p).toEqual({ uploaded: true, inProgressGameId: "g1", ready: false });
    expect(onboardingProgress([game("q1", "queued")], null).inProgressGameId).toBe("q1");
  });

  it("does not count a failed game as uploaded", () => {
    // A failed game is not progress: step one must stay open so the user
    // still has the upload link.
    expect(onboardingProgress([game("f1", "failed")], null)).toEqual({
      uploaded: false,
      inProgressGameId: null,
      ready: false,
    });
    expect(onboardingProgress([game("f1", "failed"), game("q1", "queued")], null).uploaded).toBe(true);
  });

  it("is ready once one of the user's games is ready", () => {
    expect(onboardingProgress([game("f1", "failed"), game("r1", "ready")], null).ready).toBe(true);
  });

  it("excludes the sample game", () => {
    const games = [game("sample", "ready")];
    expect(onboardingProgress(games, null).ready).toBe(true);
    expect(onboardingProgress(games, "sample")).toEqual({
      uploaded: false,
      inProgressGameId: null,
      ready: false,
    });
  });
});
