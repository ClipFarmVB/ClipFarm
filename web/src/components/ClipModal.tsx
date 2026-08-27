"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, ChevronLeft, ChevronRight, Link2, Download } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { type Clip, getClipDownloadUrl, getClipShareUrl } from "@/lib/api";
import { startCrossOriginDownload } from "@/lib/download";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";

interface ClipModalProps {
  clip: Clip;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
}

export function ClipModal({ clip, onClose, onPrev, onNext }: ClipModalProps) {
  const videoRef  = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  // Guards a double-click: two presigns and two downloads of the same clip.
  const [downloading, setDownloading] = useState(false);

  // Auto-play when clip changes
  useEffect(() => {
    videoRef.current?.play();
  }, [clip.id]);

  // Prevents the background page from jumping when the modal opens or closes.
  useBodyScrollLock(true);

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

  async function handleDownload() {
    if (downloading) return;
    setDownloading(true);
    try {
      const { url } = await getClipDownloadUrl(clip.id);
      // Not <a download>: that attribute is ignored for cross-origin URLs and
      // R2 is a different origin, so the file is named by the
      // Content-Disposition header the api asked R2 to send. lib/download.ts
      // explains why the request goes through a hidden frame rather than
      // window.location — briefly, an R2 error would otherwise render in place
      // of the app.
      startCrossOriginDownload(url);
    } catch {
      alert("Could not prepare the download.");
    } finally {
      setDownloading(false);
    }
  }

  return createPortal(
    <div
      ref={overlayRef}
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
              onClick={handleDownload}
              disabled={downloading}
              className="flex h-9 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-medium text-muted hover:text-foreground hover:bg-surface-high transition-colors disabled:opacity-50 sm:h-auto sm:py-1.5"
            >
              <Download size={12} />
              Download
            </button>
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
