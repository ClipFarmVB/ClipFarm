/**
 * State for the first-upload onboarding panel on the Library (CF-221).
 *
 * Completion lives in localStorage, per user — no api column, no migration.
 * The trigger is derived rather than signalled: the panel shows when nothing
 * is stored for this user *and* they have no ready game of their own. So an
 * existing user with a ready game gets "done" written silently and never sees
 * it, and a brand-new user sees it until they finish a game or skip.
 *
 * Every storage touch is wrapped. Safari throws on the `window.localStorage`
 * access itself when site data is blocked, not only on get/set. When storage
 * is unusable the record falls back to module memory, so a Skip still holds
 * across client-side navigation; it comes back after a full reload, which is
 * accepted.
 */
import { useSyncExternalStore } from "react";
import type { Game } from "@/lib/api";

export type OnboardingRecord = "done" | "skipped";

/**
 * What the panel reads. "none" = no record, show it if the games allow;
 * "hidden" = no user or no browser yet, never show.
 */
export type OnboardingSnapshot = OnboardingRecord | "none" | "hidden";

const KEY_PREFIX = "cf-onboarding:";

/** Per user, because browsers are shared between accounts. */
export function onboardingKey(userId: string): string {
  return `${KEY_PREFIX}${userId}`;
}

// This tab's copy of each record, so a write sticks even when storage is
// blocked or throws. Written alongside storage, never instead of it, and read
// before it: once this tab has written a record, that value wins.
const memory = new Map<string, OnboardingRecord>();
const listeners = new Set<() => void>();

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isRecord(v: unknown): v is OnboardingRecord {
  return v === "done" || v === "skipped";
}

export function readOnboarding(userId: string): OnboardingRecord | null {
  const remembered = memory.get(userId);
  if (remembered) return remembered;
  try {
    const stored = storage()?.getItem(onboardingKey(userId));
    return isRecord(stored) ? stored : null;
  } catch {
    return null;
  }
}

export function writeOnboarding(userId: string, value: OnboardingRecord): void {
  memory.set(userId, value);
  try {
    storage()?.setItem(onboardingKey(userId), value);
  } catch {
    // Memory already holds it for this page's lifetime.
  }
  listeners.forEach((l) => l());
}

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  // Another tab finishing or skipping onboarding hides it here too.
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key.startsWith(KEY_PREFIX)) onChange();
  };
  if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onChange);
    if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
  };
}

// "hidden" on the server so the SSR markup never contains the panel and
// hydration can't disagree with it.
const getServerSnapshot = (): OnboardingSnapshot => "hidden";

export function useOnboardingRecord(userId: string | undefined): OnboardingSnapshot {
  return useSyncExternalStore(
    subscribe,
    () => (userId ? (readOnboarding(userId) ?? "none") : "hidden"),
    getServerSnapshot,
  );
}

export interface OnboardingProgress {
  /**
   * The user has a game of their own in the Library that has not failed. A
   * failed game does not count: step one stays open, with its upload link,
   * since re-uploading is the only way forward from a failure.
   */
  uploaded: boolean;
  /** The newest of their games still queued or processing, if any. */
  inProgressGameId: string | null;
  /** One of their own games has finished — onboarding is complete. */
  ready: boolean;
}

/**
 * Progress through the walkthrough, from the user's own games only. The sample
 * game is always `ready`; counting it would complete onboarding on first load,
 * and that `done` is stored for good. So a game is excluded by `sampleGameId`
 * or by its own `is_sample` flag, whichever arrives first.
 */
export function onboardingProgress(
  games: readonly Game[],
  sampleGameId: string | null,
): OnboardingProgress {
  const own = games.filter((g) => g.id !== sampleGameId && g.is_sample !== true);
  const active = own.find(
    (g) => g.status === "queued" || g.status === "processing" || g.status === "uploading",
  );
  return {
    uploaded: own.some((g) => g.status !== "failed"),
    inProgressGameId: active?.id ?? null,
    ready: own.some((g) => g.status === "ready"),
  };
}
