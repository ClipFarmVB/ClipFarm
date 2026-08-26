"use client";

import { useEffect, useRef, useState } from "react";
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
  // longer duplicates the cancel. Escape therefore always does something, and
  // does the nearer thing first.
  useFocusTrap(overlayRef, true, {
    onEscape: () => {
      if (creating) { cancelCreating(); return; }
      onClose();
    },
  });

  useEffect(() => {
    getCollections()
      .then(setCollections)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (creating) setTimeout(() => newNameRef.current?.focus(), 0);
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

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div
        // Matching the drawer (CF-60) and ClipModal: the Tab trap only
        // constrains the keyboard, and `aria-modal` is what stops a screen
        // reader swiping into the page behind. On the card rather than the
        // backdrop, because the dialog is the card — the two modals disagreed
        // on this until CF-227 moved both.
        role="dialog"
        aria-modal
        aria-label="Save clip to a collection"
        className="w-full max-w-[18rem] rounded-xl border border-border bg-background shadow-2xl"
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
              onClick={() => setCreating(true)}
              className="flex min-h-9 w-full items-center gap-2 rounded px-1 text-[12px] text-subtle hover:text-foreground transition-colors"
            >
              <Plus size={13} />
              New collection
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
