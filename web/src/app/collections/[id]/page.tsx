"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle, ArrowLeft, FolderOpen, X } from "lucide-react";
import { ClipCardSkeleton } from "@/components/ui/Skeleton";
import { ClipCard } from "@/components/ClipCard";
import { ClipModal } from "@/components/ClipModal";
import { CollectionPickerModal } from "@/components/CollectionPickerModal";
import {
  getCollections,
  getCollectionClips,
  removeClipFromCollection,
  getPlayers,
  type Collection,
  type Clip,
  type Player,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { RequireAuth } from "@/components/RequireAuth";

function CollectionContent() {
  const { id } = useParams<{ id: string }>();
  const [collection, setCollection] = useState<Collection | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [players, setPlayers] = useState<Player[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeClipIndex, setActiveClipIndex] = useState<number | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [savingClipId, setSavingClipId] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      getCollections(),
      getCollectionClips(id),
      getPlayers(),
    ])
      .then(([cols, clps, plrs]) => {
        setCollection(cols.find((c) => c.id === id) ?? null);
        setClips(clps);
        setPlayers(plrs);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  const handleRemove = useCallback(async (clipId: string) => {
    if (!confirm("Remove this clip from the collection?")) return;
    setRemovingId(clipId);
    try {
      await removeClipFromCollection(id, clipId);
      setClips((prev) => prev.filter((c) => c.id !== clipId));
    } catch (e) {
      alert(e instanceof Error ? e.message : "Remove failed");
    } finally {
      setRemovingId(null);
    }
  }, [id]);

  if (error) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2.5 text-[12px] text-red-400">
        <AlertCircle size={13} className="shrink-0" /> {error}
      </div>
    );
  }

  return (
    <div className="fade-up">
      {/* Header */}
      <div className="mb-6">
        <Link
          href="/collections"
          className="mb-3 inline-flex items-center gap-1 text-[11px] text-muted hover:text-foreground transition-colors"
        >
          <ArrowLeft size={11} />
          Collections
        </Link>
        <div className="flex items-center gap-2.5">
          <FolderOpen size={16} className="text-brand shrink-0" />
          <h1 className="text-[18px] font-semibold text-foreground tracking-tight truncate">
            {loading ? <span className="skeleton inline-block h-5 w-48 rounded" /> : (collection?.name ?? "Collection")}
          </h1>
        </div>
        {!loading && (
          <p className="mt-1 ml-[26px] text-[12px] text-muted">
            {clips.length} clip{clips.length !== 1 ? "s" : ""}
          </p>
        )}
      </div>

      {/* Loading */}
      {loading && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 stagger">
          {[...Array(8)].map((_, i) => <ClipCardSkeleton key={i} />)}
        </div>
      )}

      {/* Empty state */}
      {!loading && clips.length === 0 && (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-20 text-center">
          <FolderOpen size={28} className="text-subtle mb-3" />
          <p className="text-[13px] font-medium text-foreground">No clips yet</p>
          <p className="mt-1 text-[12px] text-muted">
            Open a game and click <strong>Save</strong> on any clip to add it here.
          </p>
        </div>
      )}

      {/* Clip grid */}
      {!loading && clips.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 stagger">
          {clips.map((clip, i) => (
            <div key={clip.id} className="relative">
              <ClipCard
                clip={clip}
                players={players}
                onPlay={() => setActiveClipIndex(i)}
                onUpdate={(updated) =>
                  setClips((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
                }
                onSave={(clipId) => setSavingClipId(clipId)}
              />
              {/* Remove from collection overlay button */}
              <button
                onClick={() => handleRemove(clip.id)}
                disabled={removingId === clip.id}
                title="Remove from collection"
                className={cn(
                  "absolute top-2 left-2 z-10 flex items-center justify-center h-5 w-5 rounded bg-black/50 text-white/70 hover:bg-red-500/80 hover:text-white transition-all opacity-0 group-hover:opacity-100",
                  "[.group:hover_&]:opacity-100",
                  removingId === clip.id && "opacity-100 bg-red-500/60"
                )}
              >
                <X size={10} strokeWidth={2.5} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Collection picker */}
      {savingClipId && (
        <CollectionPickerModal
          clipId={savingClipId}
          onClose={() => setSavingClipId(null)}
        />
      )}

      {/* Clip modal */}
      {activeClipIndex !== null && clips[activeClipIndex] && (
        <ClipModal
          clip={clips[activeClipIndex]}
          onClose={() => setActiveClipIndex(null)}
          onPrev={activeClipIndex > 0 ? () => setActiveClipIndex((i) => i! - 1) : undefined}
          onNext={activeClipIndex < clips.length - 1 ? () => setActiveClipIndex((i) => i! + 1) : undefined}
        />
      )}
    </div>
  );
}

export default function CollectionPage() {
  return (
    <RequireAuth>
      <CollectionContent />
    </RequireAuth>
  );
}
