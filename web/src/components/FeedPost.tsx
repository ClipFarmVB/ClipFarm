"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Download,
  Heart,
  MessageCircle,
  Play,
  Share2,
  Volume2,
  VolumeX,
} from "lucide-react";
import type { Post } from "@/lib/api";
import { getClipDownloadUrl, getClipShareUrl, likePost, unlikePost } from "@/lib/api";
import { startCrossOriginDownload } from "@/lib/download";
import { cn } from "@/lib/utils";
import { CommentSheet } from "@/components/CommentSheet";

/** Dot colours match the landing page's action ticker. */
const ACTION_DOT: Record<string, string> = {
  spike: "bg-red-400",
  serve: "bg-sky-400",
  dig: "bg-emerald-400",
  set: "bg-violet-400",
  block: "bg-orange-400",
  unknown: "bg-white/40",
};

export interface FeedPostProps {
  post: Post;
  /** The one post in view. Exactly one at a time drives playback. */
  active: boolean;
  /**
   * Whether this post's media may hold resources. False for everything outside
   * a small window around the active post — see the effect below, which is what
   * keeps a 50-post scroll from accumulating 50 decoders.
   */
  loaded: boolean;
  muted: boolean;
  onToggleSound: () => void;
}

/**
 * One post, one viewport.
 *
 * Playback is driven from `active` rather than from a play button: the post in
 * view plays muted and loops, everything else pauses. That is the whole
 * interaction, so the video element is managed imperatively — `src` is never
 * rendered as JSX. Letting React own it would mean a re-render could reattach a
 * source we deliberately released, and releasing is the part that has to be
 * exact.
 */
export const FeedPost = memo(function FeedPost({
  post,
  active,
  loaded,
  muted,
  onToggleSound,
}: FeedPostProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  // A ref rather than state: this only serialises two rapid taps on one card,
  // and re-rendering the post to disable a button would restart nothing but
  // would put a render in the middle of a video that is playing.
  const downloading = useRef(false);
  // `play()` was refused while this post is the one on screen, or the source
  // errored. Either way the poster is all the user sees, and without an
  // affordance the only escape was to swipe away and back. State rather than a
  // ref because it renders something.
  const [stalled, setStalled] = useState(false);
  const [shareState, setShareState] = useState<"idle" | "copied" | "failed">("idle");
  // Per-card, seeded from the payload; the parent keys cards by `post.id`, so
  // this cannot bleed between posts. The server's answer replaces the guess on
  // every write — see `LikeState` in `lib/api.ts` for why both writes return
  // one.
  const [like, setLike] = useState({ liked: post.viewer_has_liked, count: post.like_count });
  const [likeNote, setLikeNote] = useState<string | undefined>(undefined);
  const likeBusy = useRef(false);
  const [commentCount, setCommentCount] = useState(post.comment_count);
  const [commentsOpen, setCommentsOpen] = useState(false);
  const { playback: pb } = post;

  /**
   * Optimistic, with rollback. The heart fills and the count moves on the tap;
   * the server's `{liked, like_count}` then replaces both — which is what makes
   * "counts match reality" something the user actually sees under concurrent
   * likes — and a failure puts the previous state back with a brief note.
   * Serialised with a ref, like `download`, so a double-tap is one request.
   */
  async function toggleLike() {
    if (likeBusy.current) return;
    likeBusy.current = true;
    const prev = like;
    setLike({ liked: !prev.liked, count: prev.count + (prev.liked ? -1 : 1) });
    try {
      const next = await (prev.liked ? unlikePost : likePost)(post.id);
      setLike({ liked: next.liked, count: next.like_count });
    } catch {
      setLike(prev);
      setLikeNote("Failed");
      window.setTimeout(() => setLikeNote(undefined), 1500);
    } finally {
      likeBusy.current = false;
    }
  }

  // Prefer the per-game proxy and seek within it; fall back to the per-clip
  // file. CF-48 populates proxy_url and CF-51 is the player this borrows from —
  // neither has landed, so `useProxy` is false for every post today and the
  // fallback is the only exercised path. The branch is here so the seam is real
  // rather than promised.
  const src = pb.proxy_url ?? pb.clip_url;
  const useProxy = pb.proxy_url != null;

  // ── attach / release the source ──────────────────────────────────────────
  //
  // Detaching is not just `src = ""`: the element keeps its buffer and its
  // decoder until `load()` is called after the attribute is gone. Skipping that
  // is exactly how a long scroll ends up holding every video it passed.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;

    if (loaded) {
      if (video.getAttribute("src") !== src) {
        video.setAttribute("src", src);
        video.load();
      }
    } else if (video.hasAttribute("src")) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
  }, [loaded, src]);

  // ── play the active post, pause the rest ─────────────────────────────────
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !loaded) return;

    if (active) {
      if (useProxy) video.currentTime = pb.start_time;
      // Applied here, before `play()`, and `muted` is a dependency of this
      // effect on purpose. `muted` is page-level state shared by every card,
      // so once the user unmutes, the *next* post to become active calls
      // `play()` unmuted from a scroll-driven effect with no user activation
      // — which iOS refuses. That refusal used to be swallowed, and because
      // `muted` was not a dependency, tapping Mute (the obvious recovery) set
      // the property and never retried: the post stayed frozen on its poster
      // with no play button anywhere, for every post after the first unmute.
      // Now a re-mute re-runs this and retries, and a refusal while this post
      // is the one on screen shows the affordance below instead of nothing.
      // A backgrounded tab still refuses too; that case clears itself the
      // moment the post becomes active again.
      video.muted = muted;
      video.play().then(
        () => setStalled(false),
        () => setStalled(true)
      );
    } else {
      setStalled(false);
      video.pause();
      if (useProxy) video.currentTime = pb.start_time;
      else video.currentTime = 0;
    }
  }, [active, loaded, useProxy, pb.start_time, muted]);

  // A tap is a user gesture, so this `play()` is allowed unmuted where the
  // effect's was not.
  const resume = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    video.play().then(
      () => setStalled(false),
      () => {}
    );
  }, []);

  // Muting is a property, not an attribute — React's `muted` prop is famously
  // not applied on hydration, so it is set here for both.
  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = muted;
  }, [muted]);

  // ── loop the clip's segment ──────────────────────────────────────────────
  //
  // Native `loop` covers the file-backed case, where the file *is* the clip.
  // A proxy holds the whole game, so looping it would play the next twenty
  // minutes; the segment is closed by hand instead.
  const onTimeUpdate = useCallback(() => {
    const video = videoRef.current;
    if (!video || !useProxy) return;
    if (video.currentTime >= pb.end_time) video.currentTime = pb.start_time;
  }, [useProxy, pb.start_time, pb.end_time]);

  const handle = post.author.username;
  const displayName = post.author.display_name || (handle ? `@${handle}` : "Someone");

  async function share() {
    try {
      const { url } = await getClipShareUrl(post.clip_id);
      await navigator.clipboard.writeText(url);
      setShareState("copied");
    } catch {
      // Clipboard is permission-gated (undefined outside a secure context,
      // rejects on a denied permission or an unfocused document) and share
      // links can 404 on a withdrawn clip. None of that is worth an error modal
      // over a video — but silence on *both* outcomes meant a user who tapped
      // Share reasonably assumed the link was on the clipboard and pasted
      // whatever was there before. The rail label swaps briefly instead.
      setShareState("failed");
    }
    window.setTimeout(() => setShareState("idle"), 1500);
  }

  /**
   * Save the clip without leaving the feed.
   *
   * Not `<a href={clip_url} download>`, which is what this was. The `download`
   * attribute is **ignored for cross-origin URLs** and R2 is a different
   * origin, so the browser simply navigates to the presigned URL — and a plain
   * presigned GET carries no `Content-Disposition`, so an mp4 renders in place
   * and the feed is gone. The user's scroll position, the page they were on,
   * all of it, for a button labelled "download" that never downloaded.
   *
   * `lib/download.ts` exists for precisely this (CF-100) and `ClipCard` already
   * uses it: `/clips/{id}/download` mints a URL that asks R2 for `attachment`
   * plus a meaningful filename, and the hidden sandboxed frame keeps the
   * navigation off the top-level context so a rejected signature cannot replace
   * the app with R2's XML error document.
   */
  async function download() {
    if (downloading.current) return;
    downloading.current = true;
    try {
      const { url } = await getClipDownloadUrl(post.clip_id);
      startCrossOriginDownload(url);
    } catch {
      // Same judgement as `share`: a failed presign over a video is not worth
      // an alert here. `ClipCard` alerts because it sits in a working grid
      // where a batch save is the task; the feed is a viewing surface.
    } finally {
      downloading.current = false;
    }
  }

  return (
    <article
      data-post-id={post.id}
      className="relative h-full w-full snap-start snap-always overflow-hidden bg-black"
    >
      {/* Tap target: the whole frame toggles sound.

          Deliberately *not* in the tab order, and not announced. It was both,
          on the reasoning that a bare onClick div is unreachable by keyboard —
          which is right in general and wrong here, because the rail below
          already carries a real, labelled Mute button for this exact action.
          As a second focusable control with the same accessible name it bought
          nothing and cost one full-screen tab stop *per post*: a 50-post scroll
          put 50 invisible buttons called "Unmute" in the tab order, ahead of
          the rail controls a keyboard user actually wants, and a screen reader
          announced each one on entering the card.

          So the pointer affordance stays and the keyboard path goes through the
          rail. `aria-hidden` with `tabIndex={-1}` is the pairing that keeps it
          out of both trees at once — either alone leaves it in the other. */}
      <button
        type="button"
        onClick={onToggleSound}
        tabIndex={-1}
        aria-hidden="true"
        className="absolute inset-0 z-10 h-full w-full cursor-default"
      />

      <video
        ref={videoRef}
        poster={pb.thumbnail_url ?? undefined}
        loop={!useProxy}
        muted
        playsInline
        preload="metadata"
        onTimeUpdate={onTimeUpdate}
        // The presigned URL is good for an hour; `post_view`'s own docstring
        // names "playback that dies mid-scroll" as the cost of that trade.
        // Without this the card showed a black frame and said nothing.
        onError={() => setStalled(true)}
        className="h-full w-full object-contain"
      />

      {stalled && active && (
        <button
          type="button"
          onClick={resume}
          aria-label="Play"
          className="absolute inset-0 z-[15] flex items-center justify-center bg-black/30 text-white"
        >
          <Play size={44} className="drop-shadow" />
        </button>
      )}

      {/* Gradient so white overlay text stays legible over a bright court. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-2/5 bg-gradient-to-t from-black/80 to-transparent" />

      {/* ── left: identity, caption, volleyball metadata ─────────────────── */}
      <div className="absolute bottom-0 left-0 z-20 max-w-[calc(100%-72px)] p-4 pb-8">
        <div className="flex items-center gap-2">
          {handle ? (
            <Link
              href={`/u/${handle}`}
              className="flex items-center gap-2 rounded-full pr-2 transition-opacity hover:opacity-80"
            >
              <Avatar url={post.author.avatar_url} name={displayName} />
              <span className="text-[13px] font-semibold text-white">{displayName}</span>
            </Link>
          ) : (
            <span className="flex items-center gap-2">
              <Avatar url={post.author.avatar_url} name={displayName} />
              <span className="text-[13px] font-semibold text-white">{displayName}</span>
            </span>
          )}
        </div>

        {post.caption && (
          <p className="mt-2 text-[13px] leading-relaxed text-white/90">{post.caption}</p>
        )}

        <div className="mt-2.5 flex items-center gap-2">
          <span className="flex items-center gap-1.5 rounded bg-white/10 px-2 py-0.5 backdrop-blur-sm">
            <span
              className={cn(
                "h-1.5 w-1.5 shrink-0 rounded-full",
                ACTION_DOT[pb.action_type] ?? ACTION_DOT.unknown
              )}
            />
            <span className="text-[10px] font-semibold uppercase tracking-widest text-white/80">
              {pb.action_type}
            </span>
          </span>
          {pb.highlight_score != null && (
            <span
              className="rounded bg-white/10 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-amber-300 backdrop-blur-sm"
              title="Highlight score"
            >
              {Math.round(pb.highlight_score * 100)}
            </span>
          )}
        </div>
      </div>

      {/* ── right: action rail ───────────────────────────────────────────── */}
      <div className="absolute bottom-8 right-2 z-20 flex flex-col items-center gap-4">
        <RailButton
          icon={<Heart size={22} className={like.liked ? "fill-red-500 text-red-500" : ""} />}
          count={like.count}
          label={like.liked ? "Unlike" : "Like"}
          note={likeNote}
          onClick={() => void toggleLike()}
        />
        <RailButton
          icon={<MessageCircle size={22} />}
          count={commentCount}
          label="Comments"
          onClick={() => setCommentsOpen(true)}
        />
        <RailButton
          icon={<Share2 size={22} />}
          label={
            shareState === "copied"
              ? "Copied"
              : shareState === "failed"
                ? "Couldn't copy"
                : "Copy share link"
          }
          note={shareState === "copied" ? "Copied" : shareState === "failed" ? "Failed" : undefined}
          onClick={share}
        />
        {pb.clip_url && (
          <RailButton
            icon={<Download size={22} />}
            label="Download clip"
            onClick={download}
          />
        )}
        <button
          type="button"
          onClick={onToggleSound}
          aria-label={muted ? "Unmute" : "Mute"}
          className="mt-1 text-white/70 transition-opacity hover:opacity-100"
        >
          {muted ? <VolumeX size={20} /> : <Volume2 size={20} />}
        </button>
      </div>

      {commentsOpen && (
        <CommentSheet
          post={post}
          onClose={() => setCommentsOpen(false)}
          onCountChange={(delta) => setCommentCount((c) => Math.max(0, c + delta))}
        />
      )}
    </article>
  );
});

function Avatar({ url, name }: { url: string | null; name: string }) {
  if (url) {
    // Plain <img>, matching the sidebar: avatars are presigned R2 URLs with a
    // fresh signature each request, so next/image would cache-miss every time —
    // and the R2 host isn't in next.config images.remotePatterns anyway.
    return (
      /* eslint-disable-next-line @next/next/no-img-element -- the R2
         host isn't in next.config images.remotePatterns */
      <img
        src={url}
        alt=""
        className="h-8 w-8 shrink-0 rounded-full border border-white/20 object-cover"
      />
    );
  }
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-white/20 bg-white/10 text-[11px] font-semibold text-white">
      {name.replace("@", "").charAt(0).toUpperCase()}
    </span>
  );
}

function RailButton({
  icon,
  count,
  label,
  note,
  onClick,
  disabled,
}: {
  icon: React.ReactNode;
  count?: number;
  label: string;
  /** A transient word under the icon — the visible half of a state change
   *  that `aria-label` already carries for assistive tech. */
  note?: string;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={cn(
        "flex flex-col items-center gap-1 text-white/90 transition-opacity",
        disabled ? "cursor-default opacity-50" : "hover:opacity-70"
      )}
    >
      {icon}
      {count != null && (
        <span className="text-[11px] font-semibold tabular-nums">{count}</span>
      )}
      {note && <span className="text-[11px] font-semibold">{note}</span>}
    </button>
  );
}
