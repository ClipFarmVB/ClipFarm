import { describe, expect, it } from "vitest";

import { FRAME_POOL_LIMIT, startCrossOriginDownload, type DownloadHost } from "./download";

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
  return { host, appended, pool: [] as HTMLIFrameElement[] };
}

describe("startCrossOriginDownload", () => {
  it("navigates a hidden frame rather than the page", () => {
    const { host, appended, pool } = stubHost();

    startCrossOriginDownload("https://r2.example/signed", host, pool);

    expect(appended).toHaveLength(1);
    expect(appended[0].src).toBe("https://r2.example/signed");
    expect(appended[0].hidden).toBe(true);
  });

  // The reason the helper exists. `window.location.href = url` renders R2's XML
  // error document in place of the app when the presigned GET is rejected —
  // expired signature, clock skew, a 5xx — and takes any unsaved edit with it.
  // Off the top-level browsing context, that response has nowhere to render.
  it("keeps the failure inside the frame", () => {
    const { host, appended, pool } = stubHost();
    startCrossOriginDownload("https://r2.example/signed", host, pool);
    expect(appended[0].style).toEqual({ display: "none" });
  });

  // Without it, a response that renders rather than downloads can run script
  // and, given user activation, navigate the top-level context — which is the
  // thing the frame is here to prevent.
  it("sandboxes the frame down to the one capability it needs", () => {
    const { host, appended, pool } = stubHost();
    startCrossOriginDownload("https://r2.example/signed", host, pool);
    expect(appended[0].sandbox).toBe("allow-downloads");
  });

  // The frame cannot be removed while the transfer it started is still running:
  // Firefox and Safari tie the request to the frame's lifetime. The first
  // version removed it on a 60s timer, which aborted any download slower than
  // that — a 40MB clip on mobile — silently, since this module has no way to
  // report an R2-side failure.
  it("does not reclaim a frame on a timer", () => {
    const { host, appended, pool } = stubHost();

    startCrossOriginDownload("https://r2.example/signed", host, pool);

    expect(appended[0].removed).toBe(false);
    expect(pool).toHaveLength(1);
  });

  it("reclaims the oldest frame once the pool is full", () => {
    const { host, appended, pool } = stubHost();

    for (let i = 0; i <= FRAME_POOL_LIMIT; i++) {
      startCrossOriginDownload(`https://r2.example/signed/${i}`, host, pool);
    }

    // Only the first is gone, and only after FRAME_POOL_LIMIT later downloads
    // began — so cutting a transfer short now takes nine at once, where the
    // timer needed one slow one.
    expect(appended[0].removed).toBe(true);
    expect(appended.slice(1).every((f) => !f.removed)).toBe(true);
    expect(pool).toHaveLength(FRAME_POOL_LIMIT);
  });
});
