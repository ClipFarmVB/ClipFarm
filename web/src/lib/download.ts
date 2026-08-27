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
 *
 * Everything below is about one consequence of that trade: **a frame removed
 * while its transfer is still running kills the download and reports nothing.**
 * Firefox and Safari tie the request to the frame's lifetime, which is why the
 * frame cannot be removed synchronously — and that fact does not stop being
 * true later, which is what the first two attempts at reclaiming frames each
 * got wrong in turn.
 */

/** The slice of `document` this needs — injected so it is testable under
 *  vitest's `node` environment, the way `classLock` takes its target. */
export interface DownloadHost {
  createElement(tag: "iframe"): HTMLIFrameElement;
  body: { appendChild(node: Node): void };
}

/** A frame and when its navigation started, which is what makes an eviction
 *  decision possible: how long a frame has existed is the only evidence
 *  available here about whether its transfer is done. */
export interface PooledFrame {
  frame: HTMLIFrameElement;
  startedAt: number;
}

/**
 * How many frames may accumulate before the oldest *eligible* one is reclaimed.
 *
 * Generous on purpose. The first version removed each frame 60s after
 * appending it, reasoning that 60s is long enough for R2 to answer — but
 * answering is not what the frame is needed for, so the timer was an abort of
 * whatever was still streaming. A 40MB clip over ~3 Mbps needs about 107
 * seconds. No constant fixes that: a slow enough connection beats any of them.
 *
 * Count-based reclamation replaced it, which was better and still not right —
 * see MIN_FRAME_AGE_MS. An empty sandboxed `display:none` frame costs
 * essentially nothing, so this is set where a batch-download session never
 * reaches it rather than at the smallest number that bounds the DOM.
 */
export const FRAME_POOL_LIMIT = 32;

/**
 * How old a frame must be before it may be reclaimed at all.
 *
 * This is the half the count alone got wrong. Eviction is FIFO, so the frame
 * it takes is the one that started earliest — and a frame is only still
 * interesting *because* its transfer has not finished, which makes the oldest
 * entry disproportionately the slow one. The policy preferentially reaped
 * exactly what it existed to protect: start a 40MB clip on mobile, then save
 * eight short ones over the next minute — an ordinary batch session, and the
 * reason CF-101 exists — and the ninth append killed the 40MB transfer.
 *
 * So age gates eviction and count only triggers it. Ten minutes is far past
 * any plausible transfer of a clip, and unlike the original timer nothing is
 * ever removed *on* a schedule: a frame is reclaimed only when later downloads
 * need the room, and only if it is old enough to be almost certainly finished.
 * When every frame is too young the pool simply grows past the limit, which is
 * the right way to be wrong — some empty elements, rather than a coach's
 * download dying silently.
 */
export const MIN_FRAME_AGE_MS = 10 * 60_000;

const framePool: PooledFrame[] = [];

export function startCrossOriginDownload(
  url: string,
  host: DownloadHost = document,
  pool: PooledFrame[] = framePool,
  now: () => number = Date.now,
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
  //
  // Note the failure mode if an engine does not honour that token: an
  // unrecognised sandbox token is ignored while the sandbox itself still
  // applies, so the download is blocked outright rather than degraded — and
  // this module cannot report that. Current Chrome, Firefox and Safari all
  // support it; the pre-merge browser pass has to confirm a file actually
  // *saves*, not merely that its name is right.
  frame.setAttribute("sandbox", "allow-downloads");
  frame.style.display = "none";
  frame.src = url;
  host.body.appendChild(frame);

  pool.push({ frame, startedAt: now() });
  // Stops at the first frame too young to touch, so the queue stays in order
  // and a live transfer is never skipped over to reach an older one.
  while (
    pool.length > FRAME_POOL_LIMIT &&
    now() - pool[0].startedAt >= MIN_FRAME_AGE_MS
  ) {
    pool.shift()?.frame.remove();
  }
}
