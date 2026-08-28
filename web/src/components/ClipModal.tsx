"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, ChevronLeft, ChevronRight, Link2, Download, Send } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { PostComposerModal } from "@/components/PostComposerModal";
import { SOCIAL_ENABLED } from "@/lib/features";
import { needsHandle, useMe } from "@/lib/useMe";
import { type Clip, getClipDownloadUrl, getClipShareUrl } from "@/lib/api";
import { startCrossOriginDownload } from "@/lib/download";
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
  const cardRef = useRef<HTMLDivElement>(null);
  // Serialises the presign call only — see the note in ClipCard's handler.
  const [downloading, setDownloading] = useState(false);

  // Auto-play when clip changes
  useEffect(() => {
    videoRef.current?.play();
  }, [clip.id]);

  // Prevents the background page from jumping when the modal opens or closes.
  useBodyScrollLock(true);

  // Focus handling, shared with the drawer and the collection picker (CF-227):
  // focus moves into the dialog on open and Tab cycles inside it rather than
  // walking into the grid behind the backdrop.
  //
  // It does NOT return focus to the clip card on close, which is the one thing
  // CF-227 asks for and does not get. The trap does call restoreFocusTo — it
  // just has nothing to restore to: the card's thumbnail is a bare
  // `<div onClick>` (ClipCard.tsx), so clicking it leaves activeElement on
  // <body>, and that is what gets captured and handed back. Tracked as
  // CF-273 (#308); until that lands, closing a clip leaves focus at the top of
  // the page rather than near the clip.
  //
  // No onEscape — the handler below already owns Escape for this overlay, and
  // passing it here too would close the modal twice.
  //
  // The card, not the backdrop: the trap should bound the same element that
  // claims to be the dialog. They hold the same controls today only because the
  // card is the backdrop's sole child — a fact about the current markup, not a
  // guarantee.
  useFocusTrap(cardRef, true, {
    // The dialog card, not a control inside it. Two things that rules out:
    // the default is the first FOCUSABLE, which since CF-100 (#305) added a
    // Download button ahead of it is no longer "Copy link" but "Download",
    // where the first Space or Enter after opening fires a presign request
    // instead of overwriting the clipboard. The control changed under this
    // and the hazard did not, which is the case for focusing neither; and
    // focusing the player instead makes the arrow keys belong to the video, so
    // ← → stop navigating clips until the user Tabs away — while the footer
    // still advertises them. A tabindex="-1" container has neither problem:
    // nothing to activate, and arrows reach the handler below.
    // Load-bearing beyond focus: an AT announces the dialog because the
    // element receiving focus is the one carrying role="dialog" and the
    // accessible name. Moving role/aria-label to the backdrop — which the
    // comment on the card warns against for the trap boundary — would also
    // silence that announcement, and nothing here would fail.
    initialFocus: () => cardRef.current,
  });

  // Which clip the composer was opened for, rather than a bare boolean.
  // Derived, so the composer closes itself when the clip changes underneath it
  // — otherwise a draft written for one clip stays mounted over the next and
  // Post publishes it against footage the user never chose, possibly at a
  // different visibility. Syncing that with an effect would be a cascading
  // render; this needs no effect at all.
  const [composingFor, setComposingFor] = useState<string | null>(null);
  const composing = composingFor === clip.id;

  // Posting needs a claimed handle. `PostAuthor` withholds a *generated* one —
  // the CF-107 backfill derives handles from email local parts, so publishing
  // one is an existence oracle — which means a handle-less author posts a card
  // that names nobody and links nowhere.
  //
  // The model comment on `User.username` has always said "the frontend blocks
  // posting until one is claimed"; until now nothing did. The claim banner in
  // layout.tsx is already the route out, so this hides the entry point rather
  // than inventing a second prompt.
  const me = useMe(SOCIAL_ENABLED);
  const canPost = SOCIAL_ENABLED && me !== null && !needsHandle(me);

  // Keyboard navigation
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // The composer owns the keyboard while it's open. Otherwise ← to fix a
      // typo in the caption navigates to the previous clip.
      //
      // Escape is NOT handled here — the composer registers a focus trap and
      // is therefore innermost (CF-282), so its `onEscape` closes it and this
      // listener must not also fire. Closing the composer from both places
      // happened to agree, which is the problem with leaving it: it reads as
      // load-bearing, and the day the two handlers do different things the
      // duplicate is invisible. Bailing on every key is the whole rule.
      if (composing) return;
      if (e.key === "Escape") {
        // Leaving the player's fullscreen fires Escape at the page as well as
        // at the UA, so without this one press exits fullscreen AND closes the
        // modal — the user asked for one of those. This is the only overlay
        // with a <video>, which is why only this one needs the guard.
        if (document.fullscreenElement) return;
        onClose();
        return;
      }
      // Arrows belong to the player while it holds focus — they seek, and the
      // shadow-DOM controls keep `activeElement` on the <video> host however
      // deep Tab has walked, so host identity is the right test. Focus starts
      // on the dialog card rather than the player precisely so ← → navigate
      // from the state the modal opens in; this guard hands them back once the
      // player has focus, whether the user Tabbed to it or clicked it.
      if (document.activeElement === videoRef.current) return;
      if (e.key === "ArrowLeft")  onPrev?.();
      if (e.key === "ArrowRight") onNext?.();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onPrev, onNext, composing]);

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
      <div
        ref={cardRef}
        // Matching the drawer (CF-60): the Tab trap only constrains the
        // keyboard, and `aria-modal` is what asks a screen reader not to swipe
        // into the grid behind. On the card rather than the backdrop, because
        // the dialog is the card.
        //
        // "Asks", not "stops". `aria-modal` is honoured by convention and its
        // support across AT/browser pairs is uneven; where it is not honoured
        // nothing else here prevents that swipe. CF-227's card asked for the
        // page behind to be `inert`, which would not depend on convention —
        // this is a substitution, and the gap is CF-285 (#333).
        role="dialog"
        aria-modal
        aria-label={`${clip.action_type} clip`}
        // Focusable, but not a Tab stop — FOCUSABLE excludes tabindex="-1".
        // This is where focus lands on open; see useFocusTrap below.
        tabIndex={-1}
        // focus:outline-none because tabIndex={-1} above makes this a focus
        // target rather than a control: opened from the keyboard,
        // :focus-visible matches the programmatic focus and the UA rings the
        // whole dialog. Nothing global suppresses it — globals.css scopes its
        // only `outline: none` to input[type="range"] — and a mouse-opened
        // modal never shows it, which is how this survives a click-through.
        className="relative flex h-full w-full flex-col overflow-hidden border-border bg-surface focus:outline-none sm:h-auto sm:max-w-4xl sm:rounded-xl sm:border sm:shadow-2xl sm:shadow-black/60"
      >
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
              aria-busy={downloading}
              title="Download this clip"
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
          {canPost && (
          <button
            onClick={() => setComposingFor(clip.id)}
            className="flex items-center gap-1.5 rounded px-2 py-1 text-[11px] text-muted hover:bg-surface-high hover:text-foreground transition-colors focus-ring"
            title="Post this clip"
          >
            <Send size={12} />
            Post
          </button>
          )}
          {/* Keyboard hints are desktop-only (CF-60) — there is no Esc or arrow
              key on a phone, and the row was pushing the Post button off-screen. */}
          <div className="ml-auto hidden items-center gap-2 text-[10px] text-subtle sm:flex">
            {/* Gated on the same props as the chevrons above: with no sibling
                clip there is nothing for ← → to navigate to, and the hint was
                advertising a binding that did nothing.

                Not gated on focus, and so not unconditionally true: the guard
                in the keydown handler hands ← → to the player while it holds
                focus, which `Tab to player` beside this is the instruction for
                reaching. It is true in the state the modal opens in, and false
                only after a deliberate move by the user. Gating it properly
                means tracking player focus in state and re-rendering on
                focus/blur — a behaviour change, ruled out of scope for CF-227
                on the PR. Recorded rather than papered over, because an
                earlier version of the PR body claimed this footer was true
                regardless of where focus sits, and that claim was false. */}
            {(onPrev || onNext) && (
              <>
                <span>← →  navigate</span>
                <span className="w-px h-3 bg-border" />
              </>
            )}
            {/* "Tab to player" was false: the header buttons precede the
                video in document order, so a forward Tab from the card lands
                on Copy link in every shape, and shift-Tab reaches the player
                only in the single-clip one. What is true regardless is that
                Tab cycles the dialog's controls, which is what the trap
                guarantees. */}
            <span>Esc close · Tab cycles controls</span>
          </div>
        </div>
      </div>
      {composing && (
        <PostComposerModal clip={clip} onClose={() => setComposingFor(null)} />
      )}
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
