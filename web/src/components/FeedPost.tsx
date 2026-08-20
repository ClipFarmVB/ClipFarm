"use client";

import { useCallback, useEffect, useRef } from "react";
import Link from "next/link";
import {
  Download,
  Heart,
  MessageCircle,
  Share2,
  Volume2,
  VolumeX,
} from "lucide-react";
import type { Post } from "@/lib/api";
import { getClipShareUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

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
export function FeedPost({ post, active, loaded, muted, onToggleSound }: FeedPostProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const { playback: pb } = post;

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
      // Autoplay is refused while a tab is backgrounded, and unmuted playback
      // is refused without a gesture. Neither is an error worth surfacing —
      // the post simply sits on its first frame until the user interacts.
      void video.play().catch(() => {});
    } else {
      video.pause();
      if (useProxy) video.currentTime = pb.start_time;
      else video.currentTime = 0;
    }
  }, [active, loaded, useProxy, pb.start_time]);

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
    } catch {
      // Clipboard is permission-gated and share links can 404 on a withdrawn
      // clip. Neither is worth an error modal over a video.
    }
  }

  return (
    <article
      data-post-id={post.id}
      className="relative h-full w-full snap-start snap-always overflow-hidden bg-black"
    >
      {/* Tap target: the whole frame toggles sound. A button rather than a div
          so it is reachable by keyboard and announced, which a bare onClick
          handler on the video would not be. */}
      <button
        type="button"
        onClick={onToggleSound}
        aria-label={muted ? "Unmute" : "Mute"}
        className="absolute inset-0 z-10 h-full w-full cursor-default"
      >
        <span className="sr-only">{muted ? "Unmute" : "Mute"}</span>
      </button>

      <video
        ref={videoRef}
        poster={pb.thumbnail_url ?? undefined}
        loop={!useProxy}
        muted
        playsInline
        preload="metadata"
        onTimeUpdate={onTimeUpdate}
        className="h-full w-full object-contain"
      />

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
        {/* Like and comment render with their real counts but do nothing until
            CF-113 ships the writes. Showing them inert beats hiding them: the
            counts are already in the payload, and a rail that changes shape
            when engagement lands is a worse first impression than one that
            fills in. */}
        <RailButton
          icon={<Heart size={22} className={post.viewer_has_liked ? "fill-red-500 text-red-500" : ""} />}
          count={post.like_count}
          label="Likes — coming with CF-113"
          disabled
        />
        <RailButton
          icon={<MessageCircle size={22} />}
          count={post.comment_count}
          label="Comments — coming with CF-113"
          disabled
        />
        <RailButton icon={<Share2 size={22} />} label="Copy share link" onClick={share} />
        {pb.clip_url && (
          <a
            href={pb.clip_url}
            download
            onClick={(e) => e.stopPropagation()}
            className="flex flex-col items-center gap-1 text-white/90 transition-opacity hover:opacity-70"
            aria-label="Download clip"
          >
            <Download size={22} />
          </a>
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
    </article>
  );
}

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
  onClick,
  disabled,
}: {
  icon: React.ReactNode;
  count?: number;
  label: string;
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
    </button>
  );
}
