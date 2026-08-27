import { describe, expect, it } from "vitest";

import {
  FRAME_POOL_LIMIT,
  MIN_FRAME_AGE_MS,
  startCrossOriginDownload,
  type DownloadHost,
  type PooledFrame,
} from "./download";

interface StubFrame extends Record<string, unknown> {
  removed: boolean;
}

function stubHost() {
  const appended: StubFrame[] = [];
  const host: DownloadHost = {
    createElement: () => {
      const el: StubFrame = {
        hidden: false,
        src: "",
        removed: false,
        style: {} as CSSStyleDeclaration,
        setAttribute(name: string, value: string) { el[name] = value; },
        remove() { el.removed = true; },
      };
      return el as unknown as HTMLIFrameElement;
    },
    body: { appendChild: (node) => { appended.push(node as unknown as StubFrame); } },
  };
  // A clock the test drives, so age-gated eviction is exercised without
  // waiting ten minutes for it.
  let clock = 0;
  return {
    host,
    appended,
    pool: [] as PooledFrame[],
    now: () => clock,
    advance: (ms: number) => { clock += ms; },
  };
}

describe("startCrossOriginDownload", () => {
  it("navigates a hidden frame rather than the page", () => {
    const { host, appended, pool, now } = stubHost();

    startCrossOriginDownload("https://r2.example/signed", host, pool, now);

    expect(appended).toHaveLength(1);
    expect(appended[0].src).toBe("https://r2.example/signed");
    expect(appended[0].hidden).toBe(true);
  });

  // The reason the helper exists. `window.location.href = url` renders R2's XML
  // error document in place of the app when the presigned GET is rejected —
  // expired signature, clock skew, a 5xx — and takes any unsaved edit with it.
  // Off the top-level browsing context, that response has nowhere to render.
  it("keeps the failure inside the frame", () => {
    const { host, appended, pool, now } = stubHost();
    startCrossOriginDownload("https://r2.example/signed", host, pool, now);
    expect(appended[0].style).toEqual({ display: "none" });
  });

  // Without it, a response that renders rather than downloads can run script
  // and, given user activation, navigate the top-level context — which is the
  // thing the frame is here to prevent.
  it("sandboxes the frame down to the one capability it needs", () => {
    const { host, appended, pool, now } = stubHost();
    startCrossOriginDownload("https://r2.example/signed", host, pool, now);
    expect(appended[0].sandbox).toBe("allow-downloads");
  });

  // Removing a frame kills the transfer it started: Firefox and Safari tie the
  // request to the frame's lifetime. The first version removed it on a 60s
  // timer, which aborted any download slower than that — a 40MB clip on mobile
  // — silently, since this module cannot report an R2-side failure.
  it("does not reclaim a frame on a timer", () => {
    const { host, appended, pool, now, advance } = stubHost();

    startCrossOriginDownload("https://r2.example/signed", host, pool, now);
    advance(MIN_FRAME_AGE_MS * 10);

    expect(appended[0].removed).toBe(false);
    expect(pool).toHaveLength(1);
  });

  // The finding against the count-only version. Eviction is FIFO, and a frame
  // is interesting precisely while its transfer is unfinished — so the oldest
  // entry is disproportionately the slow one, and count alone reaped exactly
  // what the pool exists to protect. This is the reported scenario: one long
  // download, then a batch of short ones.
  it("does not evict a young frame to make room", () => {
    const { host, appended, pool, now, advance } = stubHost();

    startCrossOriginDownload("https://r2.example/big", host, pool, now);
    for (let i = 0; i < FRAME_POOL_LIMIT + 4; i++) {
      advance(2_000);
      startCrossOriginDownload(`https://r2.example/small/${i}`, host, pool, now);
    }

    // The 40MB transfer is still running, so it is still here — the pool grew
    // past its limit rather than killing it.
    expect(appended[0].removed).toBe(false);
    expect(pool.length).toBeGreaterThan(FRAME_POOL_LIMIT);
  });

  it("reclaims the oldest frame once it is old enough to be finished", () => {
    const { host, appended, pool, now, advance } = stubHost();

    for (let i = 0; i <= FRAME_POOL_LIMIT; i++) {
      startCrossOriginDownload(`https://r2.example/signed/${i}`, host, pool, now);
    }
    expect(appended[0].removed).toBe(false);

    advance(MIN_FRAME_AGE_MS);
    startCrossOriginDownload("https://r2.example/signed/last", host, pool, now);

    expect(appended[0].removed).toBe(true);
    expect(pool.length).toBeLessThanOrEqual(FRAME_POOL_LIMIT);
  });
});
