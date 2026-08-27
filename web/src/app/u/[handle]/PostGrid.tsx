"use client";

import { useEffect, useState } from "react";
import { Globe, Loader2, Lock, Trash2, Users } from "lucide-react";
import { deletePost, getUserPosts, type Post, type Visibility } from "@/lib/api";

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
export function PostGrid({ handle, isSelf }: { handle: string; isSelf: boolean }) {
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPosts(null);
    setError(null);
    getUserPosts(handle)
      .then((data) => {
        if (!cancelled) setPosts(data);
      })
      .catch((e) => {
        // The endpoint 404s a profile the viewer can't see, which is the same
        // response as a handle that doesn't exist — by design (CF-108). The
        // profile header has already rendered by then, so an empty state reads
        // better here than an error.
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Could not load posts");
          setPosts([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [handle]);

  async function remove(id: string) {
    // No confirm dialog: the post is a pointer, so removing it destroys no
    // footage — the clip stays in the library and can be posted again. Worth
    // saying in the button's title rather than in a modal.
    setDeleting(id);
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
    </div>
  );
}
