"use client";

import { useEffect, useRef, useState } from "react";
import { FolderOpen, Plus, X, Check, Loader } from "lucide-react";
import {
  getCollections,
  createCollection,
  addClipToCollection,
  type Collection,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  clipId: string;
  onClose: () => void;
}

export function CollectionPickerModal({ clipId, onClose }: Props) {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createLoading, setCreateLoading] = useState(false);
  const newNameRef = useRef<HTMLInputElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getCollections()
      .then(setCollections)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (creating) setTimeout(() => newNameRef.current?.focus(), 0);
  }, [creating]);

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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div className="w-72 rounded-xl border border-border bg-background shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <FolderOpen size={13} className="text-brand" />
            <span className="text-[13px] font-semibold text-foreground">Save to collection</span>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-subtle hover:text-foreground hover:bg-surface-high transition-colors"
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
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreate();
                  if (e.key === "Escape") { setCreating(false); setNewName(""); }
                }}
                placeholder="Collection name…"
                maxLength={100}
                className="flex-1 rounded border border-border bg-surface px-2 py-1 text-[12px] text-foreground placeholder:text-subtle focus:border-border-strong focus:outline-none"
              />
              <button
                onClick={handleCreate}
                disabled={createLoading || !newName.trim()}
                className="rounded bg-brand px-2.5 py-1 text-[11px] font-semibold text-[#0c0c0e] disabled:opacity-40 hover:bg-brand/90 transition-colors"
              >
                {createLoading ? <Loader size={11} className="animate-spin" /> : "Add"}
              </button>
            </div>
          ) : (
            <button
              onClick={() => setCreating(true)}
              className="flex w-full items-center gap-2 rounded px-1 py-1 text-[12px] text-subtle hover:text-foreground transition-colors"
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
