"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { FeedPost } from "@/components/FeedPost";
import { RequireAuth } from "@/components/RequireAuth";
import { getFeed, type Post } from "@/lib/api";
import { isLoaded, pickActiveIndex, shouldPrefetch } from "@/lib/feedWindow";
import { SOCIAL_ENABLED } from "@/lib/features";

export default function FeedPage() {
  return (
    <RequireAuth>
      <Feed />
    </RequireAuth>
  );
}

function Feed() {
  const [posts, setPosts] = useState<Post[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [muted, setMuted] = useState(true);
  // Stable, so `FeedPost`'s `memo` can hold: an inline arrow here allocated a
  // fresh callback per render, and every `activeIndex` change re-rendered
  // every mounted card — ~500 `<article>` subtrees per swipe on the long
  // scroll the card's acceptance criteria contemplate. Main-thread cost only
  // (the media effects are dep-guarded), but it worked against the
  // "doesn't degrade" criterion the rest of the design is built around.
  const toggleSound = useCallback(() => setMuted((m) => !m), []);

  const scrollerRef = useRef<HTMLDivElement>(null);
  // Read inside the fetcher without making it a dependency — otherwise every
  // page load rebuilds the callback and re-arms the effects that call it.
  const stateRef = useRef({ cursor, done, loading });
  stateRef.current = { cursor, done, loading };

  const loadMore = useCallback(async (initial = false) => {
    const { cursor: at, done: finished, loading: busy } = stateRef.current;
    if (finished || (busy && !initial)) return;
    setLoading(true);
    try {
      const page = await getFeed(initial ? null : at);
      setPosts((prev) => {
        // De-dupe defensively. The keyset cursor guarantees no overlap, but a
        // double-fired effect in dev StrictMode would otherwise render two
        // React children with the same key.
        const seen = new Set(prev.map((p) => p.id));
        return [...prev, ...page.items.filter((p) => !seen.has(p.id))];
      });
      setCursor(page.next_cursor);
      if (!page.next_cursor) setDone(true);
      setError(null);
    } catch (e) {
      // `request()` already ran the body through apiErrorMessage (CF-91), so
      // this is the server's own sentence when it wrote one — the flag being
      // off on the API, say — and a generic line when it didn't.
      setError(e instanceof Error && e.message ? e.message : "Could not load your feed.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Guarded on the flag. The `!SOCIAL_ENABLED` screen is rendered further
    // down, but this effect runs before any of that — so with social off every
    // visit fired a `/feed` request the API 404s by construction (its own
    // `social_enabled` gate never registers the router), set `error` from the
    // failure, then discarded it to render "the feed isn't switched on". A
    // request whose answer is known before it is sent.
    if (!SOCIAL_ENABLED) {
      setLoading(false);
      return;
    }
    void loadMore(true);
  }, [loadMore]);

  // ── which post is in view ────────────────────────────────────────────────
  //
  // An observer rather than a scroll handler: scroll fires continuously and
  // would need throttling plus its own geometry maths, while this reports only
  // the crossings that matter. The threshold is high because with snap points
  // one post genuinely does fill the viewport — a low threshold would flap
  // between neighbours mid-snap and restart playback twice per swipe.
  useEffect(() => {
    const root = scrollerRef.current;
    if (!root || posts.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const next = pickActiveIndex(entries);
        if (next !== null) setActiveIndex(next);
      },
      { root, threshold: 0.6 }
    );

    const children = root.querySelectorAll("[data-index]");
    children.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [posts.length]);

  // Infinite scroll, driven by position rather than by a sentinel element:
  // the sentinel would have to sit inside the snap container and would become
  // a snap point itself.
  useEffect(() => {
    if (shouldPrefetch(activeIndex, posts.length)) void loadMore();
  }, [activeIndex, posts.length, loadMore]);

  if (!SOCIAL_ENABLED) {
    return (
      <Empty
        title="The feed isn't switched on"
        body="Set NEXT_PUBLIC_SOCIAL_ENABLED=true (and SOCIAL_ENABLED on the API) to try it."
      />
    );
  }

  if (loading && posts.length === 0) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-border-strong border-t-brand" />
      </div>
    );
  }

  if (error && posts.length === 0) {
    // A retry here for the same reason the page-2 tail has one: nothing
    // re-fires on its own. The prefetch effect keys on `[activeIndex,
    // posts.length]` and a failed *first* fetch changes neither, so a blip on
    // the request that runs right after sign-in — the first screen a user
    // sees, now that `/feed` is the landing — was a static "could not load"
    // until they worked out that a full reload was the fix.
    return (
      <Empty
        title="Could not load your feed"
        body={error}
        action={
          <Button variant="secondary" onClick={() => void loadMore(true)} disabled={loading}>
            {loading ? "Retrying…" : "Try again"}
          </Button>
        }
      />
    );
  }

  if (posts.length === 0) {
    return (
      <Empty
        title="Your feed is empty"
        body="Follow some accounts, or post one of your own clips — your own posts show up here too."
        action={
          <div className="flex items-center justify-center gap-3">
            <Link href="/games">
              <Button>Post a clip</Button>
            </Link>
          </div>
        }
      />
    );
  }

  return (
    // Breaks out of the root layout's padded, max-width column: this is the one
    // route that owns the whole viewport — but it has to respect the same
    // chrome the layout reserves. Below `lg` the sidebar is a 52px fixed top
    // bar (z-20) holding the only "Open navigation" trigger, with the drawer
    // (z-40) and its backdrop (z-30) stacked above it; the layout offsets
    // content with `pt-[52px]`. This scroller used to be `inset-y-0 z-30`,
    // which painted over that bar at every width under 1024px — and since this
    // PR makes `/feed` the post-login landing, a phone user had no way to reach
    // Library, Upload or Settings at all. So: `top-[52px]` and `z-10` under
    // `lg`, where the header, backdrop and drawer all win; from `lg` the bar
    // is gone and the 220px column exists, so `top-0 left-[220px]`. It is `lg`
    // and not `md` because the column only exists from `lg` (`Sidebar.tsx`
    // translates it off-screen below that, `layout.tsx` offsets with
    // `lg:ml-[220px]`); at `md` this indented 220px for a sidebar that was not
    // there — a dead black strip on every tablet and landscape phone.
    <div
      ref={scrollerRef}
      className="fixed inset-x-0 top-[52px] bottom-0 z-10 snap-y snap-mandatory overflow-y-scroll overscroll-y-contain bg-black lg:top-0 lg:left-[220px]"
    >
      {posts.map((post, i) => (
        <div key={post.id} data-index={i} className="h-full">
          <FeedPost
            post={post}
            active={i === activeIndex}
            loaded={isLoaded(i, activeIndex)}
            muted={muted}
            onToggleSound={toggleSound}
          />
        </div>
      ))}

      {/* snap-start on both trailing elements, and it is load-bearing rather
          than cosmetic. Under `snap-type: mandatory` the container must come to
          rest on a snap point; without an alignment of their own these are not
          snap points, so the scroll rests on the last *post* and stops exactly
          their own height short — measured at 0px of 128 visible. That makes
          the error below, the only surface for a failed page-2 fetch,
          unreachable precisely when it has something to say.

          It is the same trap the prefetch comment cites for rejecting a
          sentinel loader; the reasoning was right and simply wasn't applied to
          the two elements that ended up at the bottom. */}
      {done && (
        <div className="flex h-32 snap-start items-center justify-center text-[12px] text-white/40">
          You&apos;re all caught up.
        </div>
      )}
      {error && posts.length > 0 && (
        <div className="flex h-32 snap-start flex-col items-center justify-center gap-3 px-8 text-center text-[12px] text-white/50">
          <span>{error}</span>
          {/* A retry, because reaching this state otherwise strands the reader.
              The prefetch effect keys on `[activeIndex, posts.length]`, and a
              failed page-2 fetch changes neither — so nothing re-fires on its
              own, and the only way back is to scroll up far enough to change
              `activeIndex` and then come back down. That is not a recovery a
              user can be expected to discover from a line of grey text. */}
          {/* Not `setError(null)` first: that and `loadMore`'s own
              `setLoading(true)` batch into one render that unmounts this
              panel, so the "Retrying…" state below could never paint and a
              slow retry looked like a tap that did nothing. `loadMore` clears
              `error` itself on success; while it runs the panel stays, disabled
              and labelled. */}
          <Button variant="secondary" onClick={() => void loadMore()} disabled={loading}>
            {loading ? "Retrying…" : "Try again"}
          </Button>
        </div>
      )}
    </div>
  );
}

function Empty({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center text-center">
      <h1 className="text-[15px] font-semibold text-foreground">{title}</h1>
      <p className="mt-2 max-w-[380px] text-[13px] leading-relaxed text-muted">{body}</p>
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}
