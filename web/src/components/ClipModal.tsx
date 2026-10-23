"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { X, ChevronLeft, ChevronRight, Link2, Download, Lock, Send, Tag, Scissors, User } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { DemoBadge } from "@/components/DemoGame";
import { PostComposerModal } from "@/components/PostComposerModal";
import { SOCIAL_ENABLED } from "@/lib/features";
import { needsHandle, useMe } from "@/lib/useMe";
import {
  type Clip,
  type Player,
  getClipDownloadUrl,
  getClipShareUrl,
  setClipVisibility,
  tagClip,
  trimClip,
  updateClipLabels,
} from "@/lib/api";
import { startCrossOriginDownload } from "@/lib/download";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { cn } from "@/lib/utils";

const LABEL_OPTIONS = ["spike", "serve", "dig", "set", "block", "not_an_action"];

type EditPanel = "label" | "trim";
type EditWrite = "label" | "trim" | "tag";

interface ClipModalProps {
  clip: Clip;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  /** Hand the parent the clip's row with this dialog's change applied, so
   *  nothing behind it keeps rendering the old state — the grid's badges and
   *  player, the composer's ceiling and the undo control below all read it. */
  onUpdate?: (clip: Clip) => void;
  /** The roster the player tag picks from. Passing it is also what turns on
   *  the edit bar — Label, Trim and the player tag (CF-272): a caller that
   *  passes no roster gets a player with no editing, which is what a caller
   *  that cannot write the clip should pass. Not gated on `ownsClip`, which
   *  governs visibility alone; see that prop. */
  players?: Player[];
  /** Whether the viewer owns this clip, and so may change its visibility.
   *
   *  Defaults to FALSE. `Clip` carries no owner, so this cannot be derived
   *  here, and the retraction control below is owner-only on the API side —
   *  offering it to anyone else is a button that can only answer "Clip not
   *  found" over a clip they are looking at. The collections surface is
   *  deliberately cross-owner (`list_collection_clips` spans owners), so it
   *  passes nothing and the control stays hidden there. */
  ownsClip?: boolean;
}

export function ClipModal({
  clip,
  onClose,
  onPrev,
  onNext,
  onUpdate,
  players,
  ownsClip = false,
}: ClipModalProps) {
  const videoRef  = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  // Serialises the presign call only — see the note in ClipCard's handler.
  const [downloading, setDownloading] = useState(false);

  // ── Editing (CF-272) ──────────────────────────────────────────────────────
  // Label, Trim and the player tag moved here from ClipCard, whose one-line
  // action row clipped them. All of their state is keyed to a clip id, for the
  // reason `narrowingFor` below is: ← → swap the clip under this dialog, and a
  // panel, a busy flag or an optimistic label belongs to the clip it was
  // opened on. Keyed, a panel closes itself on navigation and a write that
  // settles late cannot mark the next clip busy or repaint its labels.
  const canEdit = players !== undefined;
  const [panelFor, setPanelFor] = useState<{ id: string; panel: EditPanel } | null>(null);
  const panel = panelFor?.id === clip.id ? panelFor.panel : null;
  const [taggingFor, setTaggingFor] = useState<string | null>(null);
  const tagging = taggingFor === clip.id;
  // `${write}:${clipId}` for every write in flight. A set, not one slot per
  // kind: a slot would forget clip A's write the moment one started on clip B,
  // and ← back to A would offer its controls again mid-write.
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  const isBusy = (write: EditWrite) => busy.has(`${write}:${clip.id}`);
  function markBusy(write: EditWrite, id: string, on: boolean) {
    setBusy((prev) => {
      const next = new Set(prev);
      if (on) next.add(`${write}:${id}`);
      else next.delete(`${write}:${id}`);
      return next;
    });
  }
  // The labels shown while a label write is out, per clip.
  const [pendingLabels, setPendingLabels] = useState<Record<string, string[]>>({});
  // The clip as the parent last rendered it. A write settles onto THIS when it
  // is still the clip on screen, not onto the copy captured at click time, so
  // two writes in flight on one clip (a tag during a label change) do not
  // undo each other's field.
  const clipRef = useRef(clip);
  useEffect(() => {
    clipRef.current = clip;
  });
  // Set when a tag write settles, so the Tag button takes focus back as it
  // remounts in place of the select; otherwise focus drops to <body>.
  const refocusTagRef = useRef(false);

  // CF-194: the raw upload is deleted after its retention window, and a re-cut
  // reads from it. Undefined means an older payload without the field — treat
  // that as available so trimming isn't hidden on a stale response.
  const canTrim = clip.source_available !== false;

  // What the label panel starts from. A clip nobody has labelled carries
  // `labels: []` and the model's call in `action_type`, so the panel shows that
  // call as selected — the seed ClipCard used to write into its own state on
  // opening. `unknown` is not a label, so it seeds nothing.
  const savedLabels = clip.labels?.length
    ? clip.labels
    : clip.action_type !== "unknown"
      ? [clip.action_type]
      : [];
  const shownLabels = pendingLabels[clip.id] ?? savedLabels;
  const isDiscarded =
    shownLabels.includes("not_an_action") ||
    (clip.action_type === "unknown" && clip.confidence === 0);

  /** Apply `fields` to the freshest copy of `target` the parent has given. */
  function settle(target: Clip, fields: Partial<Clip>) {
    const base = clipRef.current.id === target.id ? clipRef.current : target;
    onUpdate?.({ ...base, ...fields });
  }

  async function handleToggleLabel(label: string) {
    const target = clip;
    if (busy.has(`label:${target.id}`)) return;
    let next: string[];
    if (label === "not_an_action") {
      next = ["not_an_action"];
    } else {
      const without = shownLabels.filter((l) => l !== "not_an_action");
      if (without.includes(label)) {
        next = without.filter((l) => l !== label);
      } else if (without.length >= 2) {
        next = [without[1], label];
      } else {
        next = [...without, label];
      }
    }
    if (next.length === 0) next = ["not_an_action"];

    setPendingLabels((p) => ({ ...p, [target.id]: next }));
    markBusy("label", target.id, true);
    try {
      const updated = await updateClipLabels(target.id, next);
      // The fields this write changed, onto the row the page already has — not
      // the PATCH echo, whose freshly presigned `clip_url` would restart the
      // video under the user's cursor (the same hazard the undo below avoids).
      settle(target, {
        labels: updated.labels,
        action_type: updated.action_type,
        confidence: updated.confidence,
      });
    } catch (err) {
      // The optimistic labels roll back below; say why, or a label that snaps
      // back reads as the UI refusing the edit (CF-304).
      alert(err instanceof Error ? err.message : "Could not update the labels.");
    } finally {
      setPendingLabels((p) => {
        const rest = { ...p };
        delete rest[target.id];
        return rest;
      });
      markBusy("label", target.id, false);
    }
  }

  async function handleTrim(startDelta: number, endDelta: number) {
    const target = clip;
    if (busy.has(`trim:${target.id}`)) return;
    markBusy("trim", target.id, true);
    try {
      const updated = await trimClip(target.id, startDelta, endDelta);
      // Unlike a label or a tag, a trim really changes the media, so the new
      // `clip_url` and thumbnail are taken and the player reloads.
      settle(target, {
        start_time: updated.start_time,
        end_time: updated.end_time,
        clip_url: updated.clip_url,
        thumbnail_url: updated.thumbnail_url,
      });
    } catch (err) {
      alert(err instanceof Error ? err.message : "Could not trim this clip.");
    } finally {
      markBusy("trim", target.id, false);
    }
  }

  async function handleTag(playerId: string) {
    const target = clip;
    if (busy.has(`tag:${target.id}`)) return;
    markBusy("tag", target.id, true);
    try {
      const updated = await tagClip(target.id, playerId);
      // Through the parent, so the grid's card and this header both show the
      // new name (CF-304 item 3).
      settle(target, { player_id: updated.player_id, player_name: updated.player_name });
    } catch (e) {
      alert(e instanceof Error ? e.message : "Could not tag this clip.");
    } finally {
      markBusy("tag", target.id, false);
      refocusTagRef.current = true;
      setTaggingFor((current) => (current === target.id ? null : current));
    }
  }

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
  // On close, focus returns to the card's play button (CF-273, #308): the trap
  // captures activeElement on open and hands it back, and the thumbnail is a
  // real <button> now rather than a bare `<div onClick>`. Safari does not
  // focus a button on mouse click or tap (see useFocusTrap.ts), so the card
  // focuses its own button before calling onPlay, and the trap captures it in
  // every engine. One limit remains: after ←/→ navigation, focus returns to
  // the card that OPENED the modal, not the one now showing — fixing that
  // needs an opener ref per clip passed in from the grid, a follow-up rather
  // than part of CF-273.
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
  // Keyed to the clip for the same reason as the error below: while a request
  // is out for clip A, clip B's button must not read "Making private…" and sit
  // disabled.
  const [narrowingFor, setNarrowingFor] = useState<string | null>(null);
  const narrowing = narrowingFor === clip.id;
  // Keyed to the clip, the way `composingFor` is and for the same reason: an
  // error belongs to the clip it happened on. Left unkeyed, a failure on clip A
  // stayed mounted under clip B once the arrow keys moved on, claiming a
  // failure that was not B's.
  const [narrowErrorFor, setNarrowErrorFor] =
    useState<{ id: string; message: string } | null>(null);
  const narrowError = narrowErrorFor?.id === clip.id ? narrowErrorFor.message : null;
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

  // Whether the "make private again" section below renders; see the comment
  // there for each condition.
  const showUndo =
    SOCIAL_ENABLED &&
    ownsClip &&
    !clip.is_sample &&
    !!clip.effective_visibility &&
    clip.effective_visibility !== "private";

  // How much of the window, above `sm`, is NOT the video (CF-272). The card is
  // content-sized there and capped at the window, so the video has to be told
  // its share up front: a percentage max-height does not resolve inside a
  // content-sized card, and with the edit bar and an open panel a 75vh video
  // overflowed a 720px-tall window and painted over the controls. Summed from
  // what is rendered: backdrop padding, header and footer always; the edit bar,
  // an open panel and the undo section when present. Generous rather than
  // exact — the cost of over-reserving is a slightly smaller video, the cost
  // of under-reserving is the overflow, which `overflow-hidden` on the video's
  // box then turns into a crop rather than an overlap.
  const chromeRem =
    7.75 + (canEdit ? 3 : 0) + (canEdit && panel ? 3.75 : 0) + (showUndo ? 6 : 0);

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
        // modal — the user asked for one of those. `PostPlayerModal` holds a
        // <video> too and carries the same guard.
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
      // The same for the player-tag select (CF-272): on a closed <select>, ←
      // and → change the chosen option, which here fires the tag write. Taking
      // them for navigation as well would tag the clip AND move off it. Only
      // the arrows — Escape above still closes from the select.
      const active = document.activeElement;
      if (active instanceof HTMLSelectElement || active instanceof HTMLInputElement) return;
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
        // `sm:max-h-…`: the card is content-sized above `sm`, and with the edit
        // bar and an open panel (CF-272) beside a 75vh player it outgrew a
        // 720px-tall window. Bounded to the backdrop's padded box, the video —
        // the only `min-h-0 flex-1` child — gives up the height instead of the
        // controls being cut off at the bottom.
        className="relative flex h-full w-full flex-col overflow-hidden border-border bg-surface focus:outline-none sm:h-auto sm:max-h-[calc(100dvh-2rem)] sm:max-w-4xl sm:rounded-xl sm:border sm:shadow-2xl sm:shadow-black/60"
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
            {/* Demo clips (CF-565): download, share and post are refused by the
                api with 409, so the modal marks the clip instead of offering
                them. */}
            {clip.is_sample ? (
              <DemoBadge className="mr-1" />
            ) : (
            <>
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
            </>
            )}
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
        <div
          className="relative flex min-h-0 flex-1 items-center overflow-hidden bg-black"
          style={{ "--clip-chrome": `${chromeRem}rem` } as CSSProperties}
        >
          <video
            ref={videoRef}
            src={clip.clip_url}
            controls
            playsInline
            className="max-h-full w-full object-contain sm:max-h-[min(75vh,calc(100dvh-var(--clip-chrome)))]"
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

        {/* Edit bar (CF-272). Label, Trim and the player tag, moved here from
            ClipCard: they are edits to a clip you are watching, and the card's
            single action row had no room for them — Trim and the tag were
            clipped off its right edge. A bar of its own rather than items in
            the header, which already holds Download, Copy link and Close and
            has no width to spare on a phone; and not the footer, whose row is
            a single non-wrapping line (see the undo section below). This one
            wraps. Panels open inline beneath it rather than over the video. */}
        {canEdit && (
          <div className="shrink-0 border-t border-border">
            <div role="group" aria-label="Edit clip" className="flex flex-wrap items-center gap-1 px-2 py-1.5 sm:px-3">
              <button
                type="button"
                onClick={() => setPanelFor(panel === "label" ? null : { id: clip.id, panel: "label" })}
                aria-expanded={panel === "label"}
                aria-controls="clip-edit-label"
                className={cn(editButton, panel === "label" ? "bg-brand/8 text-brand" : "text-muted hover:bg-surface-high hover:text-foreground")}
              >
                <Tag size={12} />
                Label
              </button>

              {/* Not on a demo clip: the api refuses its trim with 409 (CF-565). */}
              {!clip.is_sample && (
                <button
                  type="button"
                  onClick={() => setPanelFor(panel === "trim" ? null : { id: clip.id, panel: "trim" })}
                  disabled={!canTrim}
                  aria-expanded={canTrim ? panel === "trim" : undefined}
                  aria-controls={canTrim ? "clip-edit-trim" : undefined}
                  title={canTrim ? undefined : "Source video expired — this clip can no longer be re-cut"}
                  className={cn(
                    editButton,
                    panel === "trim" ? "bg-brand/8 text-brand" : "text-muted hover:bg-surface-high hover:text-foreground",
                    "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted",
                  )}
                >
                  <Scissors size={12} />
                  Trim
                </button>
              )}

              {tagging ? (
                /* `disabled` pairs with `handleTag`'s busy check, so a second
                   choice cannot be made and then dropped in silence; `onBlur`
                   checks the same flag so a blur during the write cannot close
                   the select out from under it. Both carried over from
                   ClipCard, whose comment on this control has the long form. */
                <select
                  autoFocus
                  aria-label="Tag a player"
                  disabled={isBusy("tag")}
                  aria-busy={isBusy("tag")}
                  className="h-9 rounded-md border border-border bg-surface-high px-2 text-[11px] text-foreground focus-ring disabled:opacity-50 sm:h-8"
                  onBlur={() => {
                    if (!busy.has(`tag:${clip.id}`)) setTaggingFor(null);
                  }}
                  onChange={(e) => handleTag(e.target.value)}
                  defaultValue=""
                >
                  <option value="" disabled>
                    {players && players.length > 0 ? "Player…" : "No players on your roster"}
                  </option>
                  {players?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.jersey_number != null ? `#${p.jersey_number} ` : ""}{p.name}
                    </option>
                  ))}
                </select>
              ) : (
                <button
                  type="button"
                  ref={(el) => {
                    if (el && refocusTagRef.current) {
                      refocusTagRef.current = false;
                      el.focus();
                    }
                  }}
                  onClick={() => setTaggingFor(clip.id)}
                  title="Tag a player"
                  className={cn(editButton, "max-w-[14rem] text-muted hover:bg-surface-high hover:text-foreground")}
                >
                  <User size={12} className="shrink-0" />
                  <span className="truncate">{clip.player_name ?? "Tag player"}</span>
                </button>
              )}
            </div>

            {panel === "label" && (
              <div
                id="clip-edit-label"
                role="group"
                aria-label="Labels"
                className="flex flex-wrap gap-1.5 border-t border-border px-3 py-2.5 sm:px-4"
              >
                {LABEL_OPTIONS.map((label) => {
                  const isNotAction = label === "not_an_action";
                  const active = isNotAction ? isDiscarded : shownLabels.includes(label);
                  return (
                    <button
                      key={label}
                      type="button"
                      aria-pressed={active}
                      disabled={isBusy("label")}
                      onClick={() => handleToggleLabel(label)}
                      className={cn(
                        // Sentence case for "Not an action"; `capitalize`
                        // would title-case every word of it.
                        "h-9 rounded-md px-3 text-[11px] font-medium transition-colors focus-ring disabled:opacity-40 sm:h-8",
                        !isNotAction && "capitalize",
                        active && isNotAction
                          ? "bg-red-500/10 text-red-400 ring-1 ring-red-500/20"
                          : active
                          ? "bg-brand/10 text-brand ring-1 ring-brand/20"
                          : "bg-surface-high text-muted hover:text-foreground"
                      )}
                    >
                      {isNotAction ? "Not an action" : label}
                    </button>
                  );
                })}
              </div>
            )}

            {panel === "trim" && canTrim && !clip.is_sample && (
              <div id="clip-edit-trim" className="border-t border-border px-3 py-2.5 sm:px-4">
                {/* Each edge stacks its name, steps and time on a phone, where
                    the two side by side in one line would not fit 375px. */}
                <div className="flex items-center justify-between gap-3 sm:justify-center sm:gap-10">
                  <TrimEdge
                    edge="Start"
                    at={clip.start_time}
                    steps={[{ label: "−2s", d: [-2, 0] }, { label: "+2s", d: [2, 0] }]}
                    disabled={isBusy("trim")}
                    onStep={handleTrim}
                  />
                  <span className="text-[11px] text-subtle tabular-nums">
                    {formatDuration(clip.end_time - clip.start_time)}
                  </span>
                  <TrimEdge
                    edge="End"
                    at={clip.end_time}
                    steps={[{ label: "−2s", d: [0, -2] }, { label: "+2s", d: [0, 2] }]}
                    disabled={isBusy("trim")}
                    onStep={handleTrim}
                  />
                </div>
                {isBusy("trim") && (
                  <p className="mt-1.5 text-center text-[11px] text-brand animate-pulse" role="status">
                    Re-cutting clip…
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* Footer meta */}
        <div className="flex shrink-0 items-center gap-4 border-t border-border px-3 py-2 sm:px-4">
          <span className="text-[11px] text-muted tabular-nums">
            {formatTimestamp(clip.start_time)} – {formatTimestamp(clip.end_time)}
          </span>
          <span className="text-[11px] text-subtle">
            {formatDuration(clip.end_time - clip.start_time)}
          </span>
          {canPost && !clip.is_sample && (
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
                regardless of where focus sits, and that claim was false.

                CF-272 adds a second such state: the edit bar's player-tag
                select keeps ← → too, for the same reason. Also reached only by
                a deliberate move, so the hint is left as it is. */}
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

        {/* Taking it back (CF-109b).

            The composer can widen a clip, with a confirmation, and until this
            nothing could narrow one — so publishing was a one-way door. On
            youth-sports footage that is the wrong shape: deleting the post
            does not narrow the clip, so the only undo was deleting the
            footage.

            One button, not a picker. "Make private" is the undo, it is
            unambiguous, and it needs no confirmation because it can only ever
            show a clip to fewer people. Stepping *between* the wider tiers is
            a choice rather than a retraction, and the composer already owns
            that direction with the consent the widening needs. Offering both
            here would put an unconfirmed widening one mis-click away from the
            retraction control.

            Hidden when the clip is already private — which is every clip
            nobody has posted wider — so this renders only for someone who has
            actually published, which is who it is for.

            A section of its own below the footer, not an item in the footer's
            row: that row is a single non-wrapping flex line, and the button and
            its two sentences were squeezed into whatever width it had left. */}
        {SOCIAL_ENABLED &&
          ownsClip &&
          // A demo clip is always private and the api refuses any change to
          // it (CF-565), so this never applies; said here rather than implied.
          !clip.is_sample &&
          clip.effective_visibility &&
          clip.effective_visibility !== "private" && (
          <div className="shrink-0 border-t border-border px-3 py-3 sm:px-4">
            <button
              type="button"
              disabled={narrowing}
              onClick={async () => {
                // The clip this click is for. The dialog can move on to another
                // clip while the request is out, so the in-flight flag, the
                // error and the update are all bound to this one rather than to
                // whatever `clip` is when the request settles.
                const target = clip;
                setNarrowingFor(target.id);
                setNarrowErrorFor(null);
                try {
                  const updated = await setClipVisibility(target.id, "private");
                  // The new tier on the row the page already has, not the PATCH
                  // echo: that response carries no `player_name`, and with R2 a
                  // freshly presigned `clip_url`, so swapping it in dropped the
                  // tagged player from the header and restarted the video.
                  onUpdate?.({
                    ...target,
                    effective_visibility: updated.effective_visibility ?? "private",
                  });
                } catch (e) {
                  // Already decoded by `throwApiError`; decoding again would
                  // undo the first, the way the composer's used to.
                  setNarrowErrorFor({
                    id: target.id,
                    message:
                      e instanceof Error && e.message
                        ? e.message
                        : "Could not change it back",
                  });
                } finally {
                  setNarrowingFor((current) => (current === target.id ? null : current));
                }
              }}
              className="flex items-center gap-2 text-xs text-muted hover:text-foreground disabled:opacity-50"
            >
              <Lock className="h-3.5 w-3.5" />
              {narrowing ? "Making private…" : "Make this clip private again"}
            </button>
            <p className="mt-1 text-[11px] text-muted">
              Posts of this clip stay up but will show nothing to other
              people. A link you already shared may keep working for up to an
              hour.
            </p>
            {narrowError && (
              <p role="alert" className="mt-2 text-[11px] text-red-400">
                {narrowError}
              </p>
            )}
          </div>
        )}
      </div>
      {composing && (
        <PostComposerModal
          clip={clip}
          onClose={() => setComposingFor(null)}
          // Publishing can widen the clip, and this dialog holds the copy of it
          // that decides whether the undo below is offered. Without this the
          // consent checkbox promises "you can make it private again from the
          // clip on its game's page" and then the control is nowhere until a
          // reload — the promise unreachable in exactly the flow that makes it.
          onPosted={(raisedClipTo) => {
            if (raisedClipTo) onUpdate?.({ ...clip, effective_visibility: raisedClipTo });
          }}
        />
      )}
    </div>,
    document.body
  );
}

// The edit bar's buttons, sized like the header's: 36px on a phone.
const editButton =
  "flex h-9 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-medium transition-colors focus-ring sm:h-8";

function TrimEdge({
  edge,
  at,
  steps,
  disabled,
  onStep,
}: {
  edge: "Start" | "End";
  at: number;
  steps: { label: string; d: [number, number] }[];
  disabled: boolean;
  onStep: (startDelta: number, endDelta: number) => void;
}) {
  return (
    <div className="flex flex-col items-center gap-1 sm:flex-row sm:gap-2">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-subtle">{edge}</span>
      <div role="group" aria-label={`${edge} of clip`} className="flex items-center gap-1">
        {steps.map(({ label, d }) => (
          <button
            key={label}
            type="button"
            disabled={disabled}
            onClick={() => onStep(d[0], d[1])}
            className="h-9 min-w-11 rounded-md bg-surface-high px-2.5 text-[11px] font-medium text-muted tabular-nums transition-colors hover:bg-surface-hover hover:text-foreground focus-ring disabled:opacity-40 sm:h-8"
          >
            {label}
          </button>
        ))}
      </div>
      <span className="text-[11px] text-muted tabular-nums">{formatTimestamp(at)}</span>
    </div>
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
