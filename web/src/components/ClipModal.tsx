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
import { probeDuration } from "@/lib/mediaDuration";
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
  /** Hand the parent the fields this dialog changed on the clip `id`, so
   *  nothing behind it keeps rendering the old state — the grid's badges and
   *  player, the composer's ceiling and the undo control below all read it.
   *
   *  The fields, not a whole row: the parent merges them onto its own row for
   *  that id (`{ ...row, ...fields }`, CF-272). A write can settle after the
   *  user has moved to another clip, or after another write on the same clip
   *  has landed, and a row built here from a copy of the clip would carry that
   *  copy's stale fields back over the newer ones. */
  onUpdate?: (id: string, fields: Partial<Clip>) => void;
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
  /** The length, in seconds, of the source video the clip is cut from, where
   *  the page knows it (`Game.original_duration`). A trim's re-cut stops at
   *  the end of the source although the api keeps the end it was asked for,
   *  so this is what tells the dialog how long a cut past that end will be. */
  sourceDuration?: number | null;
}

export function ClipModal({
  clip,
  onClose,
  onPrev,
  onNext,
  onUpdate,
  players,
  ownsClip = false,
  sourceDuration,
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
  // opened on. Keyed, a panel is hidden on navigation (and shown again on ←
  // back to its clip) and a write that settles late cannot mark the next clip
  // busy or repaint its labels.
  const canEdit = players !== undefined;
  const [panelFor, setPanelFor] = useState<{ id: string; panel: EditPanel } | null>(null);
  const panel = panelFor?.id === clip.id ? panelFor.panel : null;
  const [taggingFor, setTaggingFor] = useState<string | null>(null);
  const tagging = taggingFor === clip.id;
  // `${write}:${clipId}` for every write in flight. A set, not one slot per
  // kind: a slot would forget clip A's write the moment one started on clip B,
  // and ← back to A would offer its controls again mid-write.
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  // The same set, readable before the next render: the tag select can see an
  // Enter and a `change` for one pick in the same task, and the state above is
  // still the render's copy for both.
  const busyRef = useRef<Set<string>>(new Set());
  const isBusy = (write: EditWrite) => busy.has(`${write}:${clip.id}`);
  function markBusy(write: EditWrite, id: string, on: boolean) {
    if (on) busyRef.current.add(`${write}:${id}`);
    else busyRef.current.delete(`${write}:${id}`);
    setBusy(new Set(busyRef.current));
  }
  // The labels shown while a label write is out, per clip.
  const [pendingLabels, setPendingLabels] = useState<Record<string, string[]>>({});
  // The clip id whose tag write just settled, so its Tag button takes focus
  // back as it remounts in place of the select; otherwise focus drops to
  // <body>. An id, not a flag: a write that settles after ← → must not pull
  // focus to the Tag button of the clip now showing.
  const refocusTagFor = useRef<string | null>(null);
  // Whether a `change` now arriving was made by a key. On a closed <select>
  // ↑ ↓ ← → and type-ahead change the option and fire `change`, so a keyboard
  // user browsing the roster would tag whichever player the first press lands
  // on. A keyed change therefore only picks; Enter commits it. A pick from the
  // open list commits at once, as it always has.
  //
  // Set on the keydown and cleared at the end of that task: a key that moves
  // a closed select fires its `change` within the keydown's own default
  // action, before any timer can run, while a pick from the open list comes
  // later in a task of its own. Not cleared by a mousedown alone, which the
  // list does not always deliver — on macOS ↓ opens the native menu rather
  // than moving the value, and a click in that menu never reaches the select,
  // so a flag that waited for a mousedown dropped that pick on blur.
  const tagKeyedRef = useRef(false);
  const tagKeyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [tagPicked, setTagPicked] = useState(false);
  const tagSelectRef = useRef<HTMLSelectElement | null>(null);
  // A trim's re-cut, per clip: "waiting" while it is polled for, "stale" once
  // the dialog has stopped looking without seeing it land — which a re-cut
  // that came out as long as the cut before it can also cause, so the line
  // shown for it says only that a reload shows the new cut.
  const [recut, setRecut] = useState<Record<string, RecutState>>({});
  const recutState = recut[clip.id] ?? null;
  // Stops the poll for a clip, per clip id.
  const recutWatch = useRef(new Map<string, () => void>());
  // The cut the player holds for a clip while a trim's re-cut is outstanding,
  // per clip id: the stretch of the source it covers and its length; see
  // `handleTrim`.
  const recutHeld = useRef(new Map<string, HeldCut>());
  // Per clip id, what the storage object is known to hold and what it will
  // hold next; see `watchRecut`.
  const recutChain = useRef(new Map<string, RecutChain>());
  // Where a cut asked to end at `end` actually ends: ffmpeg stops at the end
  // of the source, and the api clamps only the start (CF-272 R5-M1).
  const cutEnd = (end: number) =>
    sourceDuration != null && sourceDuration > 0 ? Math.min(end, sourceDuration) : end;

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

  async function handleToggleLabel(label: string) {
    const target = clip;
    if (busyRef.current.has(`label:${target.id}`)) return;
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
      // The fields this write changed, for the page to merge onto its row —
      // not the PATCH echo, whose freshly presigned `clip_url` would restart
      // the video under the user's cursor (the same hazard the undo below
      // avoids), and whose other fields may predate a write that landed since.
      onUpdate?.(target.id, {
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
    if (busyRef.current.has(`trim:${target.id}`)) return;
    // What the player has loaded for this clip, taken before anything changes:
    // the stretch of the source it covers, and its length as measured — what
    // a re-cut has to differ from to be told apart. Kept across trims until a
    // re-cut lands, because until then the player still holds the cut it
    // opened with, however many trims are queued behind it.
    if (!recutHeld.current.has(target.id)) {
      const v = videoRef.current;
      const loaded = v && Number.isFinite(v.duration) && v.duration > 0 ? v.duration : null;
      const end = cutEnd(target.end_time);
      recutHeld.current.set(target.id, {
        start: target.start_time,
        end,
        length: loaded ?? end - target.start_time,
      });
    }
    markBusy("trim", target.id, true);
    try {
      const updated = await trimClip(target.id, startDelta, endDelta);
      // The bounds only. The api answers before the re-cut runs (it is queued
      // to a worker), and the re-cut overwrites the same storage object, so the
      // echo's `clip_url` is a fresh signature over the OLD cut: taking it
      // reloaded the player onto the old video at 0:00, paused, while the
      // footer showed the new bounds. The media is swapped by `watchRecut`
      // once the new cut is actually there.
      onUpdate?.(target.id, { start_time: updated.start_time, end_time: updated.end_time });
      watchRecut(target.id, updated);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Could not trim this clip.");
    } finally {
      markBusy("trim", target.id, false);
    }
  }

  /** Polls until the re-cut for `id` has landed, then hands the page its new
   *  media. Nothing in the api says when a re-cut finishes, and the object's
   *  URL does not change, so the signal is the cut's length: each poll mints a
   *  fresh URL (an already-fetched one may be answered from the HTTP cache)
   *  and reads that video's duration.
   *
   *  Each trim queues a re-cut, and they overwrite the object in turn, so the
   *  object steps through one length per trim: `pending` is that sequence, and
   *  a reading that differs from the last one is matched against it, dropping
   *  the steps it has passed. The last step reached is the new cut. That is
   *  what tells a shift (Start +2s, then End +2s) apart, whose new cut is as
   *  long as the old: the first re-cut's shorter length is seen on the way.
   *
   *  The length expected is that of the cut ffmpeg makes, which ends at the
   *  end of the source (`cutEnd`). Where the page does not know the source's
   *  length, a reading shorter than the last step and matching none is taken
   *  as that step cut short by the source's end.
   *
   *  A trim that asks for the stretch the player already holds — one step out
   *  and one back, or Start −2s at 0:00, which the api clamps — has nothing to
   *  swap in, and ends the watch with nothing to say. Reporting only: a failed
   *  poll is retried, and giving up leaves the playing cut and the new bounds
   *  as they are, with a line saying a reload shows the new cut. */
  function watchRecut(id: string, reply: Clip) {
    recutWatch.current.get(id)?.();
    recutWatch.current.delete(id);
    const start = reply.start_time;
    const end = cutEnd(reply.end_time);
    const expected = end - start;
    const held = recutHeld.current.get(id) ?? { start, end, length: expected };
    if (near(start, held.start) && near(end, held.end)) {
      recutHeld.current.delete(id);
      recutChain.current.delete(id);
      setRecutFor(id, null);
      return;
    }
    const chain = recutChain.current.get(id) ?? { last: held.length, pending: [] };
    // Not a step of its own when it gives the object the length the step
    // before it does — two trims past the end of the source both cut to it,
    // and the second re-cut cannot be seen landing on top of the first.
    const lastStep = chain.pending[chain.pending.length - 1];
    if (lastStep === undefined || !near(lastStep, expected)) chain.pending.push(expected);
    recutChain.current.set(id, chain);
    setRecutFor(id, "waiting");
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = Date.now() + RECUT_GIVE_UP_MS;
    const stop = () => {
      cancelled = true;
      clearTimeout(timer);
    };
    recutWatch.current.set(id, stop);
    const poll = async () => {
      try {
        const { url } = await getClipShareUrl(id);
        const length = await probeDuration(url);
        if (cancelled) return;
        if (!near(length, chain.last)) {
          const reached = chain.pending.findIndex((p) => near(p, length));
          if (reached >= 0) chain.pending.splice(0, reached + 1);
          else if (!(sourceDuration != null && sourceDuration > 0) && length < chain.pending[chain.pending.length - 1]) {
            chain.pending = [];
          }
          chain.last = length;
        }
        if (chain.pending.length === 0) {
          recutWatch.current.delete(id);
          recutHeld.current.delete(id);
          recutChain.current.delete(id);
          setRecutFor(id, null);
          // The URL the probe just read the new cut from, and the thumbnail
          // the worker uploaded with it.
          onUpdate?.(id, { clip_url: url, thumbnail_url: reply.thumbnail_url });
          return;
        }
      } catch {
        // A failed poll is a missed reading, not a failed trim; try again.
      }
      if (cancelled) return;
      if (Date.now() >= deadline) {
        recutWatch.current.delete(id);
        setRecutFor(id, "stale");
        return;
      }
      timer = setTimeout(poll, RECUT_POLL_MS);
    };
    timer = setTimeout(poll, RECUT_FIRST_POLL_MS);
  }

  function setRecutFor(id: string, state: RecutState | null) {
    setRecut((r) => {
      const rest = { ...r };
      if (state) rest[id] = state;
      else delete rest[id];
      return rest;
    });
  }

  // Stop polling with the dialog. A re-cut still running then shows on the
  // next load of the page.
  useEffect(() => {
    const watches = recutWatch.current;
    return () => {
      for (const stop of watches.values()) stop();
      watches.clear();
    };
  }, []);

  async function handleTag(playerId: string) {
    const target = clip;
    if (!playerId || busyRef.current.has(`tag:${target.id}`)) return;
    markBusy("tag", target.id, true);
    try {
      const updated = await tagClip(target.id, playerId);
      // Through the parent, so the grid's card and this header both show the
      // new name (CF-304 item 3).
      onUpdate?.(target.id, { player_id: updated.player_id, player_name: updated.player_name });
    } catch (e) {
      alert(e instanceof Error ? e.message : "Could not tag this clip.");
    } finally {
      markBusy("tag", target.id, false);
      // Only if focus is still where the write left it — on the select, or
      // dropped to <body> by a browser that blurs a control as it disables.
      // A user who has moved on to another control keeps their place.
      const active = document.activeElement;
      if (!active || active === document.body || active === tagSelectRef.current) {
        refocusTagFor.current = target.id;
      }
      setTaggingFor((current) => (current === target.id ? null : current));
    }
  }

  function openTagSelect() {
    tagKeyedRef.current = false;
    setTagPicked(false);
    setTaggingFor(clip.id);
  }

  // Auto-play when the clip changes, and when a trim's re-cut is swapped in.
  useEffect(() => {
    // A new load interrupts a play() still pending, which rejects it; that is
    // not an error worth surfacing.
    videoRef.current?.play()?.catch(() => {});
  }, [clip.id, clip.clip_url]);

  useEffect(() => () => clearTimeout(tagKeyTimer.current), []);

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
  // an open panel, the trim panel's status line (always laid out, so it does
  // not resize the video as a re-cut starts and ends) and the undo section
  // when present. Generous rather than exact — the cost of over-reserving is a
  // slightly smaller video, the cost of under-reserving is the overflow, which
  // `overflow-hidden` on the video's box then turns into a crop rather than an
  // overlap.
  const chromeRem =
    7.75 +
    (canEdit ? 3 : 0) +
    (canEdit && panel ? 3.75 : 0) +
    (canEdit && panel === "trim" ? 1.5 : 0) +
    (showUndo ? 6 : 0);

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
      // and → change the chosen option, which here picks a player for Enter
      // to commit. Taking them for navigation as well would pick one AND move
      // off the clip. Only the arrows — Escape above still closes from the
      // select.
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
                // Only while the panel is mounted: an id naming nothing is a
                // dangling reference.
                aria-controls={panel === "label" ? "clip-edit-label" : undefined}
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
                  aria-controls={canTrim && panel === "trim" ? "clip-edit-trim" : undefined}
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
                   ClipCard, whose comment on this control has the long form.

                   A key that moves the closed select's value only picks a
                   player (see `tagKeyedRef`); Enter tags with the pick, and
                   leaving the select without it drops the pick. A pick from
                   the open list tags at once — that is already a deliberate
                   choice. */
                <>
                <select
                  ref={tagSelectRef}
                  autoFocus
                  aria-label="Tag a player"
                  aria-describedby={tagPicked ? "clip-edit-tag-hint" : undefined}
                  disabled={isBusy("tag")}
                  aria-busy={isBusy("tag")}
                  className="h-9 rounded-md border border-border bg-surface-high px-2 text-[11px] text-foreground focus-ring disabled:opacity-50 sm:h-8"
                  onBlur={() => {
                    if (!busyRef.current.has(`tag:${clip.id}`)) setTaggingFor(null);
                  }}
                  onMouseDown={() => {
                    tagKeyedRef.current = false;
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      if (e.currentTarget.value) {
                        e.preventDefault();
                        handleTag(e.currentTarget.value);
                      }
                      return;
                    }
                    // Not with Alt: Alt+↓ opens the list, it does not pick.
                    if (e.altKey) return;
                    if (e.key.startsWith("Arrow") || TAG_PAGING_KEYS.has(e.key) || (e.key.length === 1 && e.key !== " ")) {
                      tagKeyedRef.current = true;
                      clearTimeout(tagKeyTimer.current);
                      tagKeyTimer.current = setTimeout(() => {
                        tagKeyedRef.current = false;
                      }, 0);
                    }
                  }}
                  onChange={(e) => {
                    if (tagKeyedRef.current) setTagPicked(true);
                    else handleTag(e.target.value);
                  }}
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
                {tagPicked && !isBusy("tag") && (
                  <span id="clip-edit-tag-hint" className="px-1 text-[11px] text-subtle">
                    Enter to tag
                  </span>
                )}
                </>
              ) : (
                <button
                  type="button"
                  ref={(el) => {
                    // Taken once either way: on the clip whose write settled
                    // it refocuses, and on any other clip it is dropped, so
                    // coming back to that clip later does not steal focus.
                    if (el && refocusTagFor.current !== null) {
                      const settledOn = refocusTagFor.current;
                      refocusTagFor.current = null;
                      if (settledOn === clip.id) el.focus();
                    }
                  }}
                  onClick={openTagSelect}
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
                      // `aria-disabled`, not `disabled`: the chip just pressed
                      // is focused, and a browser blurs a control as it
                      // disables, dropping focus to <body> for the length of
                      // the write. `handleToggleLabel`'s busy check is what
                      // refuses the second press.
                      aria-disabled={isBusy("label") || undefined}
                      onClick={() => handleToggleLabel(label)}
                      className={cn(
                        // Sentence case for "Not an action"; `capitalize`
                        // would title-case every word of it.
                        "h-9 rounded-md px-3 text-[11px] font-medium transition-colors focus-ring aria-disabled:cursor-default aria-disabled:opacity-40 sm:h-8",
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
                {/* Always in the layout, empty when idle, so the line coming
                    and going does not resize the video on every step. */}
                <p
                  role="status"
                  className={cn(
                    "mt-1.5 min-h-4 text-center text-[11px] leading-4",
                    recutState === "stale" && !isBusy("trim") ? "text-muted" : "text-brand",
                    (isBusy("trim") || recutState === "waiting") && "animate-pulse",
                  )}
                >
                  {isBusy("trim") || recutState === "waiting"
                    ? "Re-cutting… the player shows the old cut until it’s ready"
                    : recutState === "stale"
                      ? "Couldn’t confirm the new cut here. Reload to see it."
                      : ""}
                </p>
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
                select keeps ← → too, because they move its pick. Also reached
                only by a deliberate move, so the hint is left as it is. */}
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
        {/* `showUndo` holds the conditions. A demo clip is always private
            and the api refuses any change to it (CF-565), so this never
            applies to one; said here rather than implied. */}
        {showUndo && (
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
                  // The new tier for the page's row, not the PATCH echo: that
                  // response carries no `player_name`, and with R2 a freshly
                  // presigned `clip_url`, so swapping it in dropped the tagged
                  // player from the header and restarted the video.
                  onUpdate?.(target.id, {
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
            if (raisedClipTo) onUpdate?.(clip.id, { effective_visibility: raisedClipTo });
          }}
        />
      )}
    </div>,
    document.body
  );
}

/** What a page does with `onUpdate`: the changed fields onto its row for `id`,
 *  as that row is when the write lands. Use it with a functional `setClips`,
 *  so a write that settles late merges onto the newest row, not a closure's. */
export function mergeClipFields(clips: Clip[], id: string, fields: Partial<Clip>): Clip[] {
  return clips.map((c) => (c.id === id ? { ...c, ...fields } : c));
}

type RecutState = "waiting" | "stale";

/** The cut the player holds: the stretch of the source it covers, and its
 *  length. */
interface HeldCut {
  start: number;
  end: number;
  length: number;
}

/** The storage object's length as last read, and the lengths the re-cuts
 *  still queued for it will give it, in order. */
interface RecutChain {
  last: number;
  pending: number[];
}

/** Two lengths or times the same within what a re-encode drifts by. */
function near(a: number, b: number): boolean {
  return Math.abs(a - b) < RECUT_TOLERANCE_S;
}

// Watching for a trim's re-cut (see `watchRecut`). A re-cut downloads the
// game's source and re-encodes the clip, so it takes seconds to minutes; five
// minutes without it is treated as "not this session". The tolerance is well
// under a 2s trim step and well over a re-encode's drift from its bounds.
const RECUT_FIRST_POLL_MS = 3_000;
const RECUT_POLL_MS = 5_000;
const RECUT_GIVE_UP_MS = 5 * 60_000;
const RECUT_TOLERANCE_S = 0.5;

// Keys besides the arrows that move a closed <select>'s value.
const TAG_PAGING_KEYS = new Set(["Home", "End", "PageUp", "PageDown"]);

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
            // `aria-disabled` rather than `disabled`, so focus stays on the
            // step just pressed (see the label chips); `handleTrim`'s busy
            // check refuses a step while one is out.
            aria-disabled={disabled || undefined}
            onClick={() => onStep(d[0], d[1])}
            className="h-9 min-w-11 rounded-md bg-surface-high px-2.5 text-[11px] font-medium text-muted tabular-nums transition-colors hover:bg-surface-hover hover:text-foreground focus-ring aria-disabled:cursor-default aria-disabled:opacity-40 sm:h-8"
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
