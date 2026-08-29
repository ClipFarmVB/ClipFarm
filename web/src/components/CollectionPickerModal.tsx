"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FolderOpen, Plus, X, Check, Loader } from "lucide-react";
import {
  getCollections,
  createCollection,
  addClipToCollection,
  type Collection,
} from "@/lib/api";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { cn } from "@/lib/utils";

interface Props {
  clipId: string;
  onClose: () => void;
}

export function CollectionPickerModal({ clipId, onClose }: Props) {
  // A full-screen overlay, so the page behind it must hold still like it does
  // behind the clip modal.
  //
  // It does NOT open over the clip modal, despite what this comment used to
  // say: the picker is opened from a card-footer button, and ClipModal has no
  // save affordance to open it from. Corrected while adding the focus trap
  // (CF-227), because if it were true the two traps would fight over the same
  // window listener.
  //
  // Which means this is not a live second holder of `cf-lock-scroll` either —
  // the count never exceeds 1 today, and calling the reference counting
  // load-bearing here (as this comment also used to) was the same false
  // premise wearing a different hat. `classLock.ts` and its test still carry
  // it; that is on `main` and outside this card, filed as CF-281 (#327).
  useBodyScrollLock(true);

  const [collections, setCollections] = useState<Collection[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createLoading, setCreateLoading] = useState(false);
  const newNameRef = useRef<HTMLInputElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const newCollectionRef = useRef<HTMLButtonElement>(null);

  // The third overlay in the same position (CF-227). Without a trap the modal
  // constrains Tab and offers no keyboard dismissal but the Close button, which
  // is the half-measure CF-60 named.
  //
  // Escape is routed here rather than gated off. It used to be
  // `creating ? undefined : onClose`, on the reasoning that the "new
  // collection" field "already uses Escape to cancel itself" — true only while
  // that input holds focus, because the cancel lives in its own `onKeyDown`.
  // The gate is on state, so typing a name and Tabbing to Add left Escape
  // doing nothing at all: neither cancelling the field nor closing the picker.
  // With an empty name it is shorter still — Add is disabled and so out of
  // FOCUSABLE, which makes the input `last`, so one Tab wraps to Close and
  // lands in the same dead state.
  //
  // The trap owns the key in both states instead, and the input's handler no
  // longer duplicates the cancel, so Escape does the nearer thing first:
  // cancel the field if it is open, otherwise close the picker.
  //
  // With one deliberate exception — an in-flight create, where the guard below
  // makes it do nothing. That is not the dead zone this replaced: it is a
  // refusal for the duration of a request, with a reason stated at the guard,
  // rather than a state the design forgot.
  //
  // The card, not the backdrop: the trap should bound the same element that
  // claims to be the dialog. They hold the same controls today only because the
  // card is the backdrop's sole child — a fact about the current markup, not a
  // guarantee.
  useFocusTrap(cardRef, true, {
    // The card, not the first control in it. Left to the default, focus lands
    // on the header Close button and the first Space dismisses the picker
    // before it has been read — the same hazard ClipModal avoids with "Copy
    // link", answered the same way in both overlays.
    // Load-bearing beyond focus: an AT announces the dialog because the
    // element receiving focus is the one carrying role="dialog" and the
    // accessible name. Moving role/aria-label to the backdrop — which the
    // comment on the card warns against for the trap boundary — would also
    // silence that announcement, and nothing here would fail.
    initialFocus: () => cardRef.current,
    onEscape: () => {
      // Not while the create is in flight — but only the cancel, not the close.
      // `handleCreate` clears `creating` before awaiting `handleAdd`, so
      // `createLoading` outlives the field: on a slow link the input is already
      // gone, the picker looks idle, and gating the whole handler on
      // `createLoading` swallowed Escape-to-close in that window (and
      // preventDefaulted it, so nothing else saw it either).
      //
      // Cancelling is what must not race: it does not abort the POST — the
      // collection is created and the clip saved regardless — and the
      // continuation then clears `newName`/`creating` under whatever has been
      // typed since.
      if (creating && createLoading) return;
      if (creating) { cancelCreating(); return; }
      onClose();
    },
  });

  useEffect(() => {
    getCollections()
      .then(setCollections)
      .finally(() => setLoading(false));
  }, []);

  // Focus follows the field in both directions. Opening it moves focus in;
  // closing it has to move focus back, because the input and the Add button
  // unmount and whichever held focus takes it to <body> — outside the
  // container, where the trap cannot help until the next Tab wraps it in.
  // `wasCreating` stops this stealing focus on the initial mount, where
  // `creating` is already false and nothing was unmounted.
  const wasCreatingRef = useRef(false);
  useEffect(() => {
    // Cleared on unmount: a pending callback would fire after the trap's own
    // cleanup has restored focus to the trigger, stealing it back to an element
    // that is no longer on the page.
    let t: ReturnType<typeof setTimeout> | undefined;
    if (creating) t = setTimeout(() => newNameRef.current?.focus(), 0);
    else if (wasCreatingRef.current) t = setTimeout(() => newCollectionRef.current?.focus(), 0);
    wasCreatingRef.current = creating;
    return () => { if (t !== undefined) clearTimeout(t); };
  }, [creating]);

  // Closing the inline field and discarding what was typed. Named because the
  // focus trap's Escape branch and nothing else reaches it — the field's own
  // handler used to, and no longer needs to.
  function cancelCreating() {
    setCreating(false);
    setNewName("");
  }

  async function handleAdd(collectionId: string) {
    if (saved.has(collectionId) || saving === collectionId) return;
    setSaving(collectionId);
    try {
      await addClipToCollection(collectionId, clipId);
      setSaved((prev) => new Set(prev).add(collectionId));
    } finally {
      setSaving(null);
    }
  }

  async function handleCreate() {
    const name = newName.trim();
    if (!name) return;
    // Re-entry guard. The Add button carries `disabled={createLoading || ...}`,
    // but Enter in the field reaches this directly, so two quick presses
    // created two collections. Guarding here rather than on the key covers both
    // entry points at once.
    if (createLoading) return;
    setCreateLoading(true);
    try {
      const col = await createCollection(name);
      setCollections((prev) => [col, ...prev]);
      setNewName("");
      setCreating(false);
      // Immediately add clip to the newly created collection
      await handleAdd(col.id);
    } finally {
      setCreateLoading(false);
    }
  }

  // Portalled to document.body, like ClipModal — and it is not cosmetic
  // symmetry. Rendered inline, this overlay is a descendant of the page's
  // `.fade-up` wrapper, and `.fade-up` keeps `transform: translateY(0)`
  // applied forever because its animation is `both`-filled. A transform that
  // is not `none` makes an element a containing block for `position: fixed`
  // descendants, so `inset-0` sized this to the *document* rather than the
  // viewport and centred the card thousands of pixels below the fold: mounted,
  // visible, opacity 1, and unreachable, with the scroll lock already holding
  // the page still. Measured at ovTop 76 / ovH 11304 / cardTop 5647 in a 608px
  // viewport (CF-347).
  //
  // The portal is the fix rather than editing the keyframe, because ending
  // `fade-up` at `transform: none` would still leave the bug live for the
  // 0.22s the animation is running — and because this way the overlay does not
  // care what any ancestor does. `.fade-up` wraps most pages here, so an inline
  // `fixed` child is a trap the next overlay would fall into too (CF-349).
  return createPortal(
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div
        ref={cardRef}
        // Focusable as a focus target, never as a Tab stop — see initialFocus
        // above. `trapTabWithin` handles a container holding focus that
        // FOCUSABLE does not match, which is exactly this.
        tabIndex={-1}
        // Matching the drawer (CF-60) and ClipModal: the Tab trap only
        // constrains the keyboard, and `aria-modal` is what asks a screen
        // reader not to swipe into the page behind. On the card rather than the
        // backdrop, because the dialog is the card — the two modals disagreed
        // on this until CF-227 moved both.
        //
        // "Asks", not "stops": `aria-modal` is honoured by convention and its
        // support is uneven. CF-227's card asked for the page behind to be
        // `inert`, which would not depend on convention. CF-285 (#333).
        role="dialog"
        aria-modal
        aria-label="Save clip to a collection"
        className="w-full max-w-[18rem] rounded-xl border border-border bg-background shadow-2xl focus:outline-none"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <FolderOpen size={13} className="text-brand" />
            <span className="text-[13px] font-semibold text-foreground">Save to collection</span>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded text-subtle hover:text-foreground hover:bg-surface-high transition-colors"
          >
            <X size={13} />
          </button>
        </div>

        {/* Collection list */}
        <div className="max-h-60 overflow-y-auto py-1">
          {loading && (
            <div className="flex justify-center py-6">
              <Loader size={16} className="text-subtle animate-spin" />
            </div>
          )}

          {!loading && collections.length === 0 && !creating && (
            <p className="px-4 py-4 text-center text-[12px] text-subtle">
              No collections yet — create one below.
            </p>
          )}

          {collections.map((col) => {
            const isSaved = saved.has(col.id);
            const isLoading = saving === col.id;
            return (
              <button
                key={col.id}
                onClick={() => handleAdd(col.id)}
                disabled={isSaved || isLoading}
                className={cn(
                  "flex w-full items-center justify-between px-4 py-2.5 text-left transition-colors",
                  isSaved
                    ? "text-brand cursor-default"
                    : "text-foreground hover:bg-surface-high"
                )}
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <FolderOpen size={13} className={isSaved ? "text-brand" : "text-subtle"} />
                  <span className="truncate text-[13px]">{col.name}</span>
                  <span className="text-[11px] text-subtle shrink-0">{col.clip_count}</span>
                </div>
                {isLoading ? (
                  <Loader size={12} className="animate-spin text-subtle shrink-0" />
                ) : isSaved ? (
                  <Check size={12} className="text-brand shrink-0" />
                ) : null}
              </button>
            );
          })}
        </div>

        {/* New collection */}
        <div className="border-t border-border px-3 py-2.5">
          {creating ? (
            <div className="flex items-center gap-2">
              <input
                ref={newNameRef}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                // Escape is not handled here: the focus trap owns it for the
                // whole picker, so it cancels this field wherever focus sits.
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreate();
                }}
                placeholder="Collection name…"
                maxLength={100}
                className="flex-1 rounded border border-border bg-surface px-2 py-1 text-[12px] text-foreground placeholder:text-subtle focus:border-border-strong focus:outline-none"
              />
              <button
                onClick={handleCreate}
                disabled={createLoading || !newName.trim()}
                className="shrink-0 rounded bg-brand px-3 py-2 text-[11px] font-semibold text-[#0c0c0e] disabled:opacity-40 hover:bg-brand/90 transition-colors"
              >
                {createLoading ? <Loader size={11} className="animate-spin" /> : "Add"}
              </button>
            </div>
          ) : (
            <button
              ref={newCollectionRef}
              onClick={() => setCreating(true)}
              className="flex min-h-9 w-full items-center gap-2 rounded px-1 text-[12px] text-subtle hover:text-foreground transition-colors"
            >
              <Plus size={13} />
              New collection
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
