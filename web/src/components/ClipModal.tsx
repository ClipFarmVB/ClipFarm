"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X, ChevronLeft, ChevronRight, Link2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { type Clip, getClipShareUrl } from "@/lib/api";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";
import { useFocusTrap } from "@/lib/useFocusTrap";

interface ClipModalProps {
  clip: Clip;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
}

export function ClipModal({ clip, onClose, onPrev, onNext }: ClipModalProps) {
  const videoRef  = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  // Auto-play when clip changes
  useEffect(() => {
    videoRef.current?.play();
  }, [clip.id]);

  // Prevents the background page from jumping when the modal opens or closes.
  useBodyScrollLock(true);

  // Focus handling, shared with the drawer and the collection picker (CF-227):
  // focus moves into the player on open, returns to the clip card that opened
  // it on close, and Tab cycles inside rather than walking into the grid behind
  // the backdrop.
  //
  // No onEscape — the handler below already owns Escape for this overlay, and
  // passing it here too would close the modal twice.
  useFocusTrap(overlayRef, true, {
    // The player, not the first button in the header. That default would put
    // focus on "Copy link", where the first Space or Enter after opening any
    // clip fires a request and overwrites the clipboard — pre-CF-227 focus sat
    // on <body> and Space was inert, so the default would be a regression.
    // Focusing the video also makes Space do what it should in a media dialog.
    initialFocus: () => videoRef.current,
  });

  // Keyboard navigation
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape")     onClose();
      if (e.key === "ArrowLeft")  onPrev?.();
      if (e.key === "ArrowRight") onNext?.();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onPrev, onNext]);

  // Click-outside: close only when clicking the overlay itself, not the modal card
  function handleOverlayClick(e: React.MouseEvent<HTMLDivElement>) {
    if (e.target === overlayRef.current) onClose();
  }

  async function handleShare() {
    try {
      const { url } = await getClipShareUrl(clip.id);
      await navigator.clipboard.writeText(url);
    } catch {
      alert("Could not generate share link.");
    }
  }

  return createPortal(
    <div
      ref={overlayRef}
      // Matching the drawer (CF-60): the Tab trap only constrains the keyboard.
      // `aria-modal` is what stops a screen reader swiping into the grid behind.
      role="dialog"
      aria-modal
      className="fixed inset-0 z-50 flex h-[100dvh] items-center justify-center bg-black/80 backdrop-blur-sm sm:h-auto sm:p-4"
      onClick={handleOverlayClick}
    >
      <div className="relative flex h-full w-full flex-col overflow-hidden border-border bg-surface sm:h-auto sm:max-w-4xl sm:rounded-xl sm:border sm:shadow-2xl sm:shadow-black/60">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between gap-2 px-3 py-2.5 border-b border-border sm:px-4">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
            <Badge label={clip.action_type} action={clip.action_type} />
            {clip.player_name && (
              <span className="text-[13px] font-medium text-foreground">{clip.player_name}</span>
            )}
            <span className="hidden text-[11px] text-muted tabular-nums sm:inline">
              {Math.round(clip.confidence * 100)}% confidence
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={handleShare}
              className="flex h-9 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-medium text-muted hover:text-foreground hover:bg-surface-high transition-colors sm:h-auto sm:py-1.5"
            >
              <Link2 size={12} />
              Copy link
            </button>
            <button
              onClick={onClose}
              className="flex h-9 w-9 items-center justify-center rounded-md text-muted hover:text-foreground hover:bg-surface-high transition-colors sm:h-7 sm:w-7"
              aria-label="Close"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Video */}
        <div className="relative flex min-h-0 flex-1 items-center bg-black">
          <video
            ref={videoRef}
            src={clip.clip_url}
            controls
            playsInline
            className="max-h-full w-full object-contain sm:max-h-[75vh]"
            preload="auto"
          />

          {onPrev && (
            <button
              onClick={onPrev}
              className="absolute left-2 top-1/2 -translate-y-1/2 flex h-11 w-11 items-center justify-center rounded-full bg-black/50 text-white/70 hover:bg-black/75 hover:text-white backdrop-blur-sm transition-all sm:left-3"
              aria-label="Previous clip"
            >
              <ChevronLeft size={18} />
            </button>
          )}
          {onNext && (
            <button
              onClick={onNext}
              className="absolute right-2 top-1/2 -translate-y-1/2 flex h-11 w-11 items-center justify-center rounded-full bg-black/50 text-white/70 hover:bg-black/75 hover:text-white backdrop-blur-sm transition-all sm:right-3"
              aria-label="Next clip"
            >
              <ChevronRight size={18} />
            </button>
          )}
        </div>

        {/* Footer meta */}
        <div className="flex shrink-0 items-center gap-4 border-t border-border px-3 py-2 sm:px-4">
          <span className="text-[11px] text-muted tabular-nums">
            {formatTimestamp(clip.start_time)} – {formatTimestamp(clip.end_time)}
          </span>
          <span className="text-[11px] text-subtle">
            {formatDuration(clip.end_time - clip.start_time)}
          </span>
          <div className="ml-auto hidden items-center gap-2 text-[10px] text-subtle sm:flex">
            <span>← →  navigate</span>
            <span className="w-px h-3 bg-border" />
            <span>Esc  close</span>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}

function formatTimestamp(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

function formatDuration(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
