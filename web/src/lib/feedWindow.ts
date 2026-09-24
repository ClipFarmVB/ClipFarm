/**
 * Which feed posts may hold media, and when to fetch the next page (CF-112).
 *
 * Pulled out of the component because these two rules are the card's
 * acceptance criteria rather than presentation: "scrolling 50+ posts doesn't
 * leak video elements or degrade" is entirely a question of how many indices
 * `isLoaded` admits at once, and "infinite scroll consumes CF-111's cursor" is
 * entirely `shouldPrefetch`. Both are pure, so they can be asserted without a
 * DOM — which this suite doesn't have.
 */

/**
 * How many posts either side of the active one keep their video source.
 *
 * 1 is the smallest window that satisfies both halves of the requirement: the
 * next post is preloaded so a swipe doesn't stall on a cold start, and the
 * previous one survives a scroll back up. Raising it multiplies concurrent
 * decoders for no perceptible gain — the user can only swipe one post at a
 * time, and a released post reattaches in a frame.
 */
export const LOAD_RADIUS = 1;

/** Fetch the next page once the active post is this close to the end. */
export const PREFETCH_WITHIN = 3;

/**
 * Whether the post at `index` may hold a decoder and a buffer.
 *
 * The bound is on the *window*, not on the list: it returns true for at most
 * `2 * radius + 1` indices no matter how long the feed grows, which is the
 * property that makes a 500-post scroll cost the same as a 5-post one.
 */
export function isLoaded(index: number, active: number, radius = LOAD_RADIUS): boolean {
  return Math.abs(index - active) <= radius;
}

/**
 * Whether to ask for another page.
 *
 * Position-driven rather than sentinel-driven: a sentinel element inside a
 * `scroll-snap-type: mandatory` container becomes a snap point of its own, so
 * the feed would snap to a blank loader between the last post and the next
 * page.
 */
export function shouldPrefetch(
  active: number,
  count: number,
  within = PREFETCH_WITHIN,
): boolean {
  return active >= count - within;
}


/**
 * The entry that should become the active post, or null to keep the current one.
 *
 * Split out because the callback body is the only part of the observer worth
 * testing: whether `IntersectionObserver` fires is the browser's job, but
 * picking the *right* entry out of a batch is ours — and a batch really does
 * carry several entries mid-swipe, so "take the last intersecting one" quietly
 * depends on an ordering the spec doesn't promise.
 *
 * Takes the most-visible intersecting entry instead. At a settled snap point
 * exactly one entry clears the threshold and the choice is trivial; mid-swipe
 * two do, and the larger ratio is the one the user is actually looking at.
 */
export function pickActiveIndex(
  entries: Pick<IntersectionObserverEntry, "isIntersecting" | "intersectionRatio" | "target">[],
): number | null {
  let best: { index: number; ratio: number } | null = null;
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    const raw = (entry.target as HTMLElement).dataset?.index;
    const index = Number(raw);
    if (raw == null || Number.isNaN(index)) continue;
    if (!best || entry.intersectionRatio > best.ratio) best = { index, ratio: entry.intersectionRatio };
  }
  return best ? best.index : null;
}
