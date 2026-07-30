"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertCircle, FolderOpen, Plus, Trash2, Pencil, Check, X } from "lucide-react";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { getCollections, createCollection, renameCollection, deleteCollection, type Collection } from "@/lib/api";
import { cn } from "@/lib/utils";

function CollectionsContent() {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createLoading, setCreateLoading] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const newNameRef = useRef<HTMLInputElement>(null);
  const renameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getCollections()
      .then(setCollections)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (creating) setTimeout(() => newNameRef.current?.focus(), 0);
  }, [creating]);

  useEffect(() => {
    if (renamingId) setTimeout(() => renameRef.current?.select(), 0);
  }, [renamingId]);

  async function handleCreate() {
    const name = newName.trim();
    if (!name) return;
    setCreateLoading(true);
    try {
      const col = await createCollection(name);
      setCollections((prev) => [col, ...prev]);
      setNewName("");
      setCreating(false);
    } finally {
      setCreateLoading(false);
    }
  }

  async function commitRename(id: string) {
    const name = renameValue.trim();
    setRenamingId(null);
    if (!name) return;
    const original = collections.find((c) => c.id === id)?.name ?? "";
    if (name === original) return;
    try {
      const updated = await renameCollection(id, name);
      setCollections((prev) => prev.map((c) => (c.id === id ? updated : c)));
    } catch {
      // revert silently
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`Delete "${name}"? Clips will not be deleted.`)) return;
    setDeleting(id);
    try {
      await deleteCollection(id);
      setCollections((prev) => prev.filter((c) => c.id !== id));
    } catch (e) {
      alert(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="fade-up">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[18px] font-semibold text-foreground tracking-tight">Collections</h1>
          {!loading && (
            <p className="mt-0.5 text-[12px] text-muted">
              {collections.length === 0 ? "No collections yet" : `${collections.length} collection${collections.length !== 1 ? "s" : ""}`}
            </p>
          )}
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          <Plus size={12} strokeWidth={2.5} />
          New collection
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2.5 text-[12px] text-red-400 mb-4">
          <AlertCircle size={13} className="shrink-0" />
          {error}
        </div>
      )}

      {/* New collection input */}
      {creating && (
        <div className="flex items-center gap-2 rounded-lg border border-brand/30 bg-surface px-3 py-2.5 mb-3">
          <FolderOpen size={14} className="text-brand shrink-0" />
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
            className="flex-1 bg-transparent text-[13px] text-foreground placeholder:text-subtle focus:outline-none"
          />
          <button
            onClick={handleCreate}
            disabled={createLoading || !newName.trim()}
            className="rounded bg-brand px-2.5 py-1 text-[11px] font-semibold text-[#0c0c0e] disabled:opacity-40 hover:bg-brand/90 transition-colors"
          >
            {createLoading ? "…" : "Create"}
          </button>
          <button onClick={() => { setCreating(false); setNewName(""); }} className="text-subtle hover:text-foreground">
            <X size={13} />
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="space-y-1.5">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-14 rounded-lg border border-border bg-surface animate-pulse" />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && collections.length === 0 && !creating && (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-20 text-center">
          <div className="mb-3 h-10 w-10 rounded-full bg-surface-high border border-border flex items-center justify-center">
            <FolderOpen size={16} className="text-subtle" />
          </div>
          <p className="text-[13px] font-medium text-foreground">No collections yet</p>
          <p className="mt-1 text-[12px] text-muted">Save clips from any game to keep track of your favorites.</p>
          <Button size="sm" className="mt-4" onClick={() => setCreating(true)}>New collection</Button>
        </div>
      )}

      {/* Collection list */}
      {!loading && collections.length > 0 && (
        <div className="space-y-1">
          {/* Column headers */}
          <div className="mb-1 grid grid-cols-[1fr_80px_56px] items-center gap-4 px-3 py-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-subtle">Name</span>
            <span className="text-[10px] font-semibold uppercase tracking-widest text-subtle text-right">Clips</span>
            <span />
          </div>

          {collections.map((col) => (
            <div
              key={col.id}
              className="group grid grid-cols-[1fr_80px_56px] items-center gap-4 rounded-lg border border-border bg-surface px-3 py-3 hover:border-border-strong hover:bg-surface-high transition-all duration-150"
            >
              {renamingId === col.id ? (
                <input
                  ref={renameRef}
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={() => commitRename(col.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") e.currentTarget.blur();
                    if (e.key === "Escape") setRenamingId(null);
                  }}
                  className="w-full rounded border border-border-strong bg-surface px-1.5 py-0.5 text-[13px] font-medium text-foreground focus:outline-none focus:ring-1 focus:ring-brand"
                  maxLength={100}
                />
              ) : (
                <Link href={`/collections/${col.id}`} className="flex items-center gap-2.5 min-w-0">
                  <FolderOpen size={14} className="text-subtle shrink-0" />
                  <span className="truncate text-[13px] font-medium text-foreground group-hover:text-brand transition-colors">
                    {col.name}
                  </span>
                </Link>
              )}

              <span className="text-right text-[11px] text-muted tabular-nums">{col.clip_count}</span>

              <div className="flex items-center justify-end gap-1">
                <button
                  onClick={() => { setRenamingId(col.id); setRenameValue(col.name); }}
                  className="opacity-0 group-hover:opacity-100 flex items-center justify-center h-6 w-6 rounded text-subtle hover:text-foreground hover:bg-surface-hover transition-all"
                  title="Rename"
                >
                  <Pencil size={11} />
                </button>
                <button
                  onClick={() => handleDelete(col.id, col.name)}
                  disabled={deleting === col.id}
                  className="opacity-0 group-hover:opacity-100 flex items-center justify-center h-6 w-6 rounded text-subtle hover:text-red-400 hover:bg-red-500/10 transition-all disabled:opacity-30"
                  title="Delete collection"
                >
                  <Trash2 size={11} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function CollectionsPage() {
  return (
    <RequireAuth>
      <CollectionsContent />
    </RequireAuth>
  );
}
