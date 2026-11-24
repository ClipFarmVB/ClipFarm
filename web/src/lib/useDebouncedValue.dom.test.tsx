// @vitest-environment jsdom
//
// CF-186: the game page's clip fetch reads a debounced copy of its filters.
//
// The regression this pins: the fetch effect depended on the raw filters, and
// the sliders change them on every step of a drag. `GET /games/{id}/clips` is
// rate limited per caller at 60 a minute, so a single drag could exhaust the
// budget and replace the page with a 429 until reload.
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDebouncedValue } from "./useDebouncedValue";

const DELAY = 300;

let latest: number | undefined;
let effectRuns = 0;

/** What the page's fetch effect would do with each settled value. */
function record(settled: number) {
  latest = settled;
  effectRuns += 1;
}

/** Stands in for the page: reads the debounced value and "fetches" on change. */
function Probe({ value }: { value: number }) {
  const settled = useDebouncedValue(value, DELAY);
  useEffect(() => {
    record(settled);
  }, [settled]);
  return null;
}

let container: HTMLDivElement;
let root: Root;

function render(value: number) {
  act(() => {
    root.render(<Probe value={value} />);
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  latest = undefined;
  effectRuns = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("useDebouncedValue", () => {
  it("returns the first value immediately, so the initial load is not delayed", () => {
    render(7);
    expect(latest).toBe(7);
    expect(effectRuns).toBe(1);
  });

  it("holds a changing value back until it has been still for the delay", () => {
    render(0);
    for (let step = 1; step <= 10; step++) {
      render(step);
      act(() => {
        vi.advanceTimersByTime(DELAY - 1);
      });
      expect(latest).toBe(0);
    }
    act(() => {
      vi.advanceTimersByTime(DELAY);
    });
    expect(latest).toBe(10);
  });

  it("turns a 100-step drag into one change, not one per step", () => {
    render(0);
    for (let step = 1; step <= 100; step++) {
      render(step);
      act(() => {
        vi.advanceTimersByTime(16);
      });
    }
    act(() => {
      vi.advanceTimersByTime(DELAY);
    });
    expect(latest).toBe(100);
    // One run on mount, one for the settled drag.
    expect(effectRuns).toBe(2);
  });
});
