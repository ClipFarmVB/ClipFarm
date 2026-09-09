"use client";

import { useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import type { Post } from "@/lib/api";
import { useFocusTrap } from "@/lib/useFocusTrap";

/**
 * Watch one post from the profile grid (CF-109b item 2, #398).
 *
 * `PostGrid` deliberately rendered thumbnails and no player, on the argument
 * that "the full playback experience is CF-112's feed". True, and it left a
 * gap the card names: the profile is the only surface that shows posts, so in
 * practice a published clip could not be watched anywhere — including by its
 * own author, since your own posts are not in your feed.
 *
 * Small on purpose. This is not a second feed: no rail, no autoplay-on-scroll,
 * no prefetch. `PostOut.playback` already carries `clip_url`, so it needs no
 * API change, and `clip_url` is the CUT file rather than the source video —
 * `start_time` and `end_time` describe where it came from and are not offsets
 * into it, which is why playback starts at zero and why `ClipModal` does the
 * same.
 *
 * Portal, `z-[60]` overlay, click-outside on the overlay itself, and the shared
 * focus trap with Escape wired through `onEscape` — the same shape as
 * `PostComposerModal` and `CommentSheet`, and for the reasons those files give.
 * The card is the initial focus rather than the video: the player's own
 * controls live in a closed shadow root, so focusing it would put the user
 * somewhere Tab cannot reliably leave, and `ClipModal` lands on its card for
 * the same reason.
 */
export function PostPlayerModal({ post, onClose }: { post: Post; onClose: () => void }) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  useFocusTrap(cardRef, true, {
    initialFocus: () => cardRef.current,
    onEscape: onClose,
  });

  const pb = post.playback;

  return createPortal(
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/85 p-4"
      onClick={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}
    >
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-label={post.caption || "Post"}
        tabIndex={-1}
        className="flex w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-border bg-background"
      >
        <div className="flex items-start justify-between gap-3 px-4 py-2.5">
          <p className="min-w-0 truncate text-[13px] text-muted">
            {post.caption || " "}
          </p>
          <button
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded p-1 text-subtle hover:text-foreground"
          >
            <X size={16} />
          </button>
        </div>

        {pb.clip_url ? (
          <video
            src={pb.clip_url}
            poster={pb.thumbnail_url ?? undefined}
            controls
            autoPlay
            playsInline
            className="max-h-[70vh] w-full bg-black"
          />
        ) : (
          // A post whose clip has no readable URL. The row still exists, so
          // saying so beats an empty black box the user will click at.
          <p className="px-4 py-10 text-center text-sm text-muted">
            This clip isn&apos;t available to play right now.
          </p>
        )}
      </div>
    </div>,
    document.body,
  );
}
