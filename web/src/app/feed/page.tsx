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
    return <Empty title="Could not load your feed" body={error} />;
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
    // route that owns the whole viewport. `left-0 md:left-[220px]` clears the
    // fixed sidebar on desktop and ignores it on a phone — the sidebar's own
    // mobile behaviour is CF-60, not this card.
    <div
      ref={scrollerRef}
      className="fixed inset-y-0 right-0 left-0 z-30 snap-y snap-mandatory overflow-y-scroll overscroll-y-contain bg-black md:left-[220px]"
    >
      {posts.map((post, i) => (
        <div key={post.id} data-index={i} className="h-full">
          <FeedPost
            post={post}
            active={i === activeIndex}
            loaded={isLoaded(i, activeIndex)}
            muted={muted}
            onToggleSound={() => setMuted((m) => !m)}
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
          <Button
            variant="secondary"
            onClick={() => {
              setError(null);
              void loadMore();
            }}
            disabled={loading}
          >
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
