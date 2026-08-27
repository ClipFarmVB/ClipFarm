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
 * `attachment` downloads exactly as before; anything else renders into a
 * sandboxed element nobody can see, and the app is untouched. The cost is that
 * an R2-side failure is silent rather than loud — the caller still reports a
 * failure of the api call that mints the URL, which is the failure that is
 * actually likely here, but a signature R2 rejects will look like a click that
 * did nothing. That is a worse-to-debug outcome traded for a much less costly
 * one.
 */

/** The slice of `document` this needs — injected so it is testable under
 *  vitest's `node` environment, the way `classLock` takes its target. */
export interface DownloadHost {
  createElement(tag: "iframe"): HTMLIFrameElement;
  body: { appendChild(node: Node): void };
}

/**
 * How many frames are kept alive.
 *
 * **Not a timeout, deliberately.** The first version removed each frame 60s
 * after appending it, on the reasoning that 60s is long enough for R2 to
 * answer. It is — but answering is not what the frame is needed for. Firefox
 * and Safari tie the request to the frame's lifetime, which is why the frame
 * cannot be removed synchronously; that is equally true at 60 seconds, so the
 * timer was not a cleanup, it was an abort of whatever was still streaming. A
 * 40MB clip over a ~3 Mbps mobile connection needs about 107 seconds, and this
 * module has already traded a loud failure for a silent one — so the user
 * would have got a truncated file and no error anywhere. Sizing the constant
 * against time-to-first-byte while it bounds time-to-complete was the defect,
 * and no constant fixes it: a slow enough connection beats any of them.
 *
 * So frames are reclaimed by count instead. A frame is removed only once this
 * many *later* downloads have started, which for the failing case above means
 * the transfer is only cut short if nine downloads are in flight at once —
 * where the timer needed just one slow download to do it. What is left behind
 * meanwhile is at most eight empty, sandboxed, display:none elements.
 */
export const FRAME_POOL_LIMIT = 8;

const framePool: HTMLIFrameElement[] = [];

export function startCrossOriginDownload(
  url: string,
  host: DownloadHost = document,
  pool: HTMLIFrameElement[] = framePool,
): void {
  const frame = host.createElement("iframe");
  frame.hidden = true;
  frame.setAttribute("aria-hidden", "true");
  // Defence in depth, and it makes the claim above literally true: without a
  // sandbox, a response that renders rather than downloads is a document that
  // can run script and — given user activation — navigate the top-level
  // context, which is the thing this module exists to prevent. The URL is ours
  // against our own bucket, so this is not a live hole. `allow-downloads` is
  // the one capability kept, and it is the only one the frame is for.
  frame.setAttribute("sandbox", "allow-downloads");
  frame.style.display = "none";
  frame.src = url;
  host.body.appendChild(frame);

  pool.push(frame);
  while (pool.length > FRAME_POOL_LIMIT) pool.shift()?.remove();
}
