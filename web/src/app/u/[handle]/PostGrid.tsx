"use client";

import { useEffect, useState } from "react";
import { Globe, Loader2, Lock, Trash2, Users } from "lucide-react";
import { deletePost, getUserPosts, type Post, type Visibility } from "@/lib/api";
import { SOCIAL_ENABLED } from "@/lib/features";
import { useMe } from "@/lib/useMe";

const TIER_ICON: Record<Visibility, typeof Lock> = {
  private: Lock,
  followers: Users,
  public: Globe,
};

/**
 * The posts on a profile (CF-109).
 *
 * Publishing needed a surface that reads it back and one that takes it down.
 * `getUserPosts` and `deletePost` existed and nothing called them, so the
 * shipped loop was: post a clip, see "Posted", and then find it nowhere and be
 * unable to unpublish it without a hand-rolled DELETE. For youth-sports
 * footage "I can publish but I can't unpublish" is the wrong asymmetry, and it
 * is the half of the card's acceptance line — "deleting the clip or the post
 * removes it from all surfaces" — that was actually missing.
 *
 * Deliberately a grid of thumbnails rather than a player: the full playback
 * experience is CF-112's feed. This is the owner's inventory and the visitor's
 * proof the profile has something on it.
 */
/** Matches the API's own default. Requested explicitly so a full page is
 *  detectable rather than a number the client has to assume. */
const PAGE = 50;

export function PostGrid({ handle, isSelf }: { handle: string; isSelf: boolean }) {
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  // Whether the *server* filled the page, captured at load rather than derived
  // from `posts.length` later. Deleting one post from a full page takes the
  // array to 49 and silently retired the notice below, at exactly the moment
  // it mattered most: the older posts are still there, still unreachable, and
  // this is the only surface that can unpublish them. The count changes; what
  // the server said does not.
  const [wasFullPage, setWasFullPage] = useState(false);
  // Null when signed out. PostGrid subscribes rather than taking a prop:
  // `isSelf` above is a different question (is this MY profile) and is false
  // for a stranger both signed in and signed out, so it cannot stand in for
  // "who is asking". useMe shares one cached request across subscribers.
  const viewerId = useMe(SOCIAL_ENABLED)?.id ?? null;

  useEffect(() => {
    let cancelled = false;
    setPosts(null);
    setError(null);
    getUserPosts(handle, PAGE)
      .then((data) => {
        if (!cancelled) {
          setPosts(data);
          setWasFullPage(data.length === PAGE);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Could not load posts");
          setPosts([]);
        }
      });
    return () => {
      cancelled = true;
    };
    // `viewerId`, not just `handle`: the response is scoped to the viewer, and
    // the viewer can change without the route doing so. Signing out on your own
    // profile left every private post — tiles, tiers and thumbnails — rendered
    // to a session that could no longer request them, because the effect had no
    // reason to re-run. The private half of the grid is exactly what must not
    // survive a sign-out.
    //
    // The id rather than the object: `useMe` republishes a fresh `Me` to every
    // subscriber on avatar upload and rename, and refetching the grid because
    // someone changed their display name is a request for nothing.
    //
    // Note what this does *not* undo. The thumbnails already on screen are
    // presigned for an hour, so they stay fetchable by URL until they expire —
    // the revocation window `_serialize` documents on the api side. Clearing
    // them from the DOM is the half a client can fix; the other half is
    // CF-112's stable-URL endpoint.
  }, [handle, viewerId]);

  async function remove(id: string) {
    // No confirm dialog: the post is a pointer, so removing it destroys no
    // footage — the clip stays in the library and can be posted again. Worth
    // saying in the button's title rather than in a modal.
    setDeleting(id);
    // Clear first, or a failed delete leaves its banner up for the life of the
    // page: nothing else resets it once the load effect has run, so a retry
    // that succeeds removes the row from the grid while the message above it
    // still says the delete failed.
    setError(null);
    try {
      await deletePost(id);
      setPosts((prev) => (prev ?? []).filter((p) => p.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove that post");
    } finally {
      setDeleting(null);
    }
  }

  if (posts === null) {
    return (
      <div className="mt-8 flex justify-center py-10">
        <Loader2 className="h-5 w-5 animate-spin text-muted" />
      </div>
    );
  }

  // A failed load is not an empty profile, and the two must not render the
  // same. The catch above sets `posts` to [] so the grid has something to map,
  // which meant this early return fired first and reported "No posts yet." for
  // every failure — swallowing the message it had just stored. Error first.
  if (error && posts.length === 0) {
    return (
      <div className="mt-8 rounded-md border border-dashed border-border px-4 py-10 text-center">
        <p className="text-sm text-muted">Couldn&apos;t load posts.</p>
        <p className="mt-1 text-xs text-subtle">{error}</p>
      </div>
    );
  }

  if (posts.length === 0) {
    return (
      <div className="mt-8 rounded-md border border-dashed border-border px-4 py-10 text-center">
        <p className="text-sm text-muted">
          {isSelf
            ? "No posts yet. Open a clip from your library and hit Post."
            : "No posts yet."}
        </p>
      </div>
    );
  }

  return (
    <div className="mt-8">
      {error && (
        <p className="mb-3 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          {error}
        </p>
      )}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {posts.map((post) => {
          const Tier = TIER_ICON[post.visibility];
          return (
            <div
              key={post.id}
              className="group relative aspect-video overflow-hidden rounded-md border border-border bg-surface-high"
            >
              {post.playback.thumbnail_url ? (
                // eslint-disable-next-line @next/next/no-img-element -- presigned R2 URL, see settings/profile
                <img
                  src={post.playback.thumbnail_url}
                  alt=""
                  className="h-full w-full object-cover"
                />
              ) : (
                <div className="flex h-full items-center justify-center text-[11px] text-subtle">
                  No thumbnail
                </div>
              )}

              <span
                className="absolute left-1.5 top-1.5 rounded bg-black/60 p-1 text-white/80"
                title={post.visibility}
              >
                <Tier className="h-3 w-3" />
              </span>

              {isSelf && (
                <button
                  onClick={() => remove(post.id)}
                  disabled={deleting === post.id}
                  title="Remove this post. The clip itself stays in your library."
                  aria-label="Remove this post"
                  className="absolute right-1.5 top-1.5 rounded bg-black/60 p-1 text-white/80 opacity-0 transition-opacity hover:text-white focus-visible:opacity-100 group-hover:opacity-100 disabled:opacity-50"
                >
                  {deleting === post.id ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Trash2 className="h-3 w-3" />
                  )}
                </button>
              )}

              {post.caption && (
                <p className="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/80 to-transparent px-2 py-1.5 text-[11px] text-white/90">
                  {post.caption}
                </p>
              )}
            </div>
          );
        })}
      </div>
      {wasFullPage && (
        // The cap is deliberate (CF-109 defers cursor paging to when a profile
        // actually needs it), but it must not be *invisible*: this is the only
        // place a post can be taken down, so an author with more than a page of
        // them would otherwise have no way to know the rest exist.
        <p className="mt-3 text-center text-[11px] text-subtle">
          Showing the {PAGE} most recent posts.
          {isSelf ? " Older ones aren't listed here yet." : ""}
        </p>
      )}
    </div>
  );
}
