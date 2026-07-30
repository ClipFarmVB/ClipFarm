"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X, ChevronLeft, ChevronRight, Link2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { type Clip, getClipShareUrl } from "@/lib/api";

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

  // Lock body scroll while modal is open — prevents background page from
  // jumping when the modal is opened or closed
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
      onClick={handleOverlayClick}
    >
      <div className="relative w-full max-w-4xl rounded-xl border border-border bg-surface overflow-hidden shadow-2xl shadow-black/60">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <div className="flex items-center gap-2.5">
            <Badge label={clip.action_type} action={clip.action_type} />
            {clip.player_name && (
              <span className="text-[13px] font-medium text-foreground">{clip.player_name}</span>
            )}
            <span className="text-[11px] text-muted tabular-nums">
              {Math.round(clip.confidence * 100)}% confidence
            </span>
          </div>

          <div className="flex items-center gap-1">
            <button
              onClick={handleShare}
              className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11px] font-medium text-muted hover:text-foreground hover:bg-surface-high transition-colors"
            >
              <Link2 size={12} />
              Copy link
            </button>
            <button
              onClick={onClose}
              className="flex items-center justify-center h-7 w-7 rounded-md text-muted hover:text-foreground hover:bg-surface-high transition-colors"
              aria-label="Close"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Video */}
        <div className="relative bg-black">
          <video
            ref={videoRef}
            src={clip.clip_url}
            controls
            className="w-full max-h-[75vh] object-contain"
            preload="auto"
          />

          {onPrev && (
            <button
              onClick={onPrev}
              className="absolute left-3 top-1/2 -translate-y-1/2 flex h-9 w-9 items-center justify-center rounded-full bg-black/50 text-white/70 hover:bg-black/75 hover:text-white backdrop-blur-sm transition-all"
              aria-label="Previous clip"
            >
              <ChevronLeft size={18} />
            </button>
          )}
          {onNext && (
            <button
              onClick={onNext}
              className="absolute right-3 top-1/2 -translate-y-1/2 flex h-9 w-9 items-center justify-center rounded-full bg-black/50 text-white/70 hover:bg-black/75 hover:text-white backdrop-blur-sm transition-all"
              aria-label="Next clip"
            >
              <ChevronRight size={18} />
            </button>
          )}
        </div>

        {/* Footer meta */}
        <div className="px-4 py-2 border-t border-border flex items-center gap-4">
          <span className="text-[11px] text-muted tabular-nums">
            {formatTimestamp(clip.start_time)} – {formatTimestamp(clip.end_time)}
          </span>
          <span className="text-[11px] text-subtle">
            {formatDuration(clip.end_time - clip.start_time)}
          </span>
          <div className="ml-auto flex items-center gap-2 text-[10px] text-subtle">
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
