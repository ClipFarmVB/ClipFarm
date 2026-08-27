import { describe, expect, it } from "vitest";

import { FRAME_LIFETIME_MS, startCrossOriginDownload, type DownloadHost } from "./download";

function stubHost() {
  const appended: Record<string, unknown>[] = [];
  const removed: Record<string, unknown>[] = [];
  const host: DownloadHost = {
    createElement: () => {
      const el = {
        hidden: false,
        src: "",
        style: {} as CSSStyleDeclaration,
        setAttribute(name: string, value: string) { (el as Record<string, unknown>)[name] = value; },
        remove() { removed.push(el as unknown as Record<string, unknown>); },
      };
      return el as unknown as HTMLIFrameElement;
    },
    body: { appendChild: (node) => { appended.push(node as unknown as Record<string, unknown>); } },
  };
  return { host, appended, removed };
}

describe("startCrossOriginDownload", () => {
  it("navigates a hidden frame rather than the page", () => {
    const { host, appended } = stubHost();

    startCrossOriginDownload("https://r2.example/signed", host, () => {});

    expect(appended).toHaveLength(1);
    expect(appended[0].src).toBe("https://r2.example/signed");
    expect(appended[0].hidden).toBe(true);
  });

  // The reason the helper exists. `window.location.href = url` renders R2's XML
  // error document in place of the app when the presigned GET is rejected —
  // expired signature, clock skew, a 5xx — and takes any unsaved edit with it.
  // Off the top-level browsing context, that response has nowhere to render.
  it("keeps the failure inside the frame", () => {
    const { host, appended } = stubHost();
    startCrossOriginDownload("https://r2.example/signed", host, () => {});
    expect(appended[0].style).toEqual({ display: "none" });
  });

  it("leaves the frame in place long enough for R2 to answer", () => {
    const { host, removed } = stubHost();
    const scheduled: Array<[() => void, number]> = [];

    startCrossOriginDownload("https://r2.example/signed", host, (fn, ms) => {
      scheduled.push([fn, ms]);
    });

    // Firefox and Safari tie the request to the frame's lifetime, so removing
    // it synchronously cancels the download it was created to start.
    expect(removed).toHaveLength(0);
    expect(scheduled[0][1]).toBe(FRAME_LIFETIME_MS);

    scheduled[0][0]();
    expect(removed).toHaveLength(1);
  });
});
