/**
 * Starting a cross-origin download without navigating the app away (CF-100).
 *
 * `<a download>` is ignored for cross-origin URLs — R2 is a different origin —
 * so the file is named by the `Content-Disposition: attachment` header the api
 * asks R2 to send, and the browser has to be pointed at the URL for that header
 * to arrive.
 *
 * The obvious way to point it, `window.location.href = url`, is the wrong one.
 * A response that carries `attachment` never commits a navigation, so it looks
 * like nothing happened and the page stays put — but a response that *doesn't*
 * is rendered in place: an expired signature, a clock skew, an R2 5xx, and the
 * user is looking at R2's XML error document where the app used to be, with
 * their unsaved label edits gone. The failure mode we can't rule out is exactly
 * the one that costs the most.
 *
 * A hidden iframe takes the same navigation off the top-level browsing context.
 * `attachment` downloads exactly as before; anything else renders into an
 * element nobody can see and the app is untouched. The cost is that an R2-side
 * failure is silent rather than loud — the caller still reports a failure of the
 * api call that mints the URL, which is the failure that is actually likely
 * here, but a signature R2 rejects will look like a click that did nothing.
 * That is a worse-to-debug outcome traded for a much less costly one.
 */

/** The slice of `document` this needs — injected so it is testable under
 *  vitest's `node` environment, the way `classLock` takes its target. */
export interface DownloadHost {
  createElement(tag: "iframe"): HTMLIFrameElement;
  body: { appendChild(node: Node): void };
}

/** How long the frame is left in the DOM. Removing it immediately cancels the
 *  download in Firefox and Safari, which tie the request to the frame's
 *  lifetime; a minute is long enough for R2 to answer and short enough that a
 *  session of downloads doesn't accumulate frames. */
export const FRAME_LIFETIME_MS = 60_000;

export function startCrossOriginDownload(
  url: string,
  host: DownloadHost = document,
  schedule: (fn: () => void, ms: number) => void = setTimeout,
): void {
  const frame = host.createElement("iframe");
  frame.hidden = true;
  frame.setAttribute("aria-hidden", "true");
  frame.style.display = "none";
  frame.src = url;
  host.body.appendChild(frame);
  schedule(() => frame.remove(), FRAME_LIFETIME_MS);
}
