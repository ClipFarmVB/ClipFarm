/**
 * Reference-counted class toggling (CF-60).
 *
 * Separated from `useBodyScrollLock` so the counting — the whole reason that
 * hook exists rather than two inline `classList` calls — is plain logic that
 * can be tested without a DOM. The target is passed as a thunk so the real
 * caller can bind `document.body.classList` lazily, and a test can hand in a
 * stub.
 *
 * The point of counting: overlays nest. The collection picker opens over the
 * clip modal, both take `cf-lock-scroll`, and the picker's cleanup must not
 * unlock the page while the modal is still up. Bare add/remove would break
 * exactly there — and before the picker took a lock at all, it survived only
 * because no two callers happened to share a class, which is an accident
 * rather than a guarantee.
 */

export interface ClassTarget {
  add(className: string): void;
  remove(className: string): void;
}

/** Side effects that run only on the first acquire and the last release. */
export interface ClassLockHooks {
  onFirstAcquire?(): void;
  onLastRelease?(): void;
}

export interface ClassLock {
  acquire(className: string, hooks?: ClassLockHooks): void;
  release(className: string, hooks?: ClassLockHooks): void;
  /** Current holder count — for assertions. */
  held(className: string): number;
}

export function createClassLock(target: () => ClassTarget): ClassLock {
  const holders = new Map<string, number>();

  return {
    acquire(className, hooks) {
      const next = (holders.get(className) ?? 0) + 1;
      holders.set(className, next);
      if (next === 1) {
        hooks?.onFirstAcquire?.();
        target().add(className);
      }
    },

    release(className, hooks) {
      // `?? 1` so an unbalanced release fails in the safe direction: it drops
      // to 0 and unlocks, rather than going negative and wedging the class on.
      const next = (holders.get(className) ?? 1) - 1;
      if (next > 0) {
        holders.set(className, next);
        return;
      }
      holders.delete(className);
      target().remove(className);
      hooks?.onLastRelease?.();
    },

    held(className) {
      return holders.get(className) ?? 0;
    },
  };
}
