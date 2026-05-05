"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertCircle, ArrowRight, Plus, Trash2 } from "lucide-react";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { GameRowSkeleton } from "@/components/ui/Skeleton";
import { getGames, deleteGame, renameGame, type Game } from "@/lib/api";
import { getCachedGames, getInflightGames, updateGamesCache, invalidateGamesCache } from "@/lib/gamesCache";
import { cn } from "@/lib/utils";

const STATUS_DOT: Record<Game["status"], string> = {
  ready:      "bg-emerald-400",
  processing: "bg-blue-400 animate-pulse",
  queued:     "bg-zinc-600",
  failed:     "bg-red-400",
};

const STATUS_LABEL: Record<Game["status"], string> = {
  queued:     "Queued",
  processing: "Processing",
  ready:      "Ready",
  failed:     "Failed",
};

function GamesContent() {
  // Initialise from cache for an instant render — no spinner if data is ready.
  const [games, setGames] = useState<Game[]>(() => getCachedGames() ?? []);
  const [loading, setLoading] = useState(() => getCachedGames() === null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [hovering, setHovering] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);

  function startRename(game: Game) {
    setRenaming(game.id);
    setRenameValue(game.title);
    setTimeout(() => renameInputRef.current?.select(), 0);
  }

  async function commitRename(gameId: string) {
    const trimmed = renameValue.trim();
    setRenaming(null);
    if (!trimmed) return;
    const original = games.find((g) => g.id === gameId)?.title ?? "";
    if (trimmed === original) return;
    try {
      const updated = await renameGame(gameId, trimmed);
      setGames((prev) => {
        const next = prev.map((g) => (g.id === gameId ? { ...g, title: updated.title } : g));
        updateGamesCache(next);
        return next;
      });
    } catch {
      // silently revert — title stays as original in state since we didn't optimistically update
    }
  }

  async function handleDelete(gameId: string, title: string) {
    if (!confirm(`Delete "${title}" and all its clips? This cannot be undone.`)) return;
    setDeleting(gameId);
    try {
      await deleteGame(gameId);
      setGames((prev) => {
        const next = prev.filter((g) => g.id !== gameId);
        updateGamesCache(next);
        return next;
      });
    } catch (e) {
      alert(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleting(null);
    }
  }

  useEffect(() => {
    const cached = getCachedGames();
    const hasActive = (list: Game[]) =>
      list.some((g) => g.status === "processing" || g.status === "queued");

    // If we have fresh data with no active jobs there's nothing to fetch.
    if (cached && !hasActive(cached)) return;

    // Re-use the in-flight prefetch started by AuthContext, or start a new one.
    const p = getInflightGames() ?? getGames();
    p.then((data) => {
      updateGamesCache(data);
      setGames(data);
      setLoading(false);
    }).catch((e: Error) => {
      setError(e.message);
      setLoading(false);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll while any game is actively processing
  useEffect(() => {
    const hasActive = games.some((g) => g.status === "processing" || g.status === "queued");
    if (!hasActive) return;
    const interval = setInterval(() => {
      getGames().then((data) => {
        updateGamesCache(data);
        setGames(data);
      }).catch(() => {});
    }, 10_000);
    return () => clearInterval(interval);
  }, [games]);

  return (
    <div className="fade-up">
      {/* Page header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[18px] font-semibold text-foreground tracking-tight">Library</h1>
          {!loading && (
            <p className="mt-0.5 text-[12px] text-muted">
              {games.length === 0
                ? "No games yet"
                : `${games.length} game${games.length !== 1 ? "s" : ""}`}
            </p>
          )}
        </div>
        <Link href="/upload">
          <Button size="sm">
            <Plus size={12} strokeWidth={2.5} />
            New game
          </Button>
        </Link>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2.5 text-[12px] text-red-400 mb-4">
          <AlertCircle size={13} className="shrink-0" />
          {error}
        </div>
      )}

      {/* Loading skeletons */}
      {loading && (
        <div className="space-y-1.5">
          {[...Array(4)].map((_, i) => (
            <GameRowSkeleton key={i} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && games.length === 0 && (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-20 text-center">
          <div className="mb-3 h-10 w-10 rounded-full bg-surface-high border border-border flex items-center justify-center">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-subtle">
              <path d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
          </div>
          <p className="text-[13px] font-medium text-foreground">No games yet</p>
          <p className="mt-1 text-[12px] text-muted">Upload game footage to start generating highlights</p>
          <Link href="/upload" className="mt-4">
            <Button size="sm">Upload now</Button>
          </Link>
        </div>
      )}

      {/* Game list */}
      {!loading && !error && games.length > 0 && (
        <div className="stagger">
          {/* Column headers */}
          <div className="mb-1 grid grid-cols-[1fr_80px_88px_48px_56px] items-center gap-4 px-3 py-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-subtle">Title</span>
            <span className="text-[10px] font-semibold uppercase tracking-widest text-subtle text-right">Date</span>
            <span className="text-[10px] font-semibold uppercase tracking-widest text-subtle text-center">Status</span>
            <span className="text-[10px] font-semibold uppercase tracking-widest text-subtle text-right">Clips</span>
            <span />
          </div>

          {/* Rows */}
          {games.map((game) => (
            <div
              key={game.id}
              onMouseEnter={() => setHovering(game.id)}
              onMouseLeave={() => setHovering(null)}
              className={cn(
                "group grid grid-cols-[1fr_80px_88px_48px_56px] items-center gap-4 rounded-lg border px-3 py-3 mb-1 transition-all duration-150",
                hovering === game.id
                  ? "border-border-strong bg-surface-high"
                  : "border-border bg-surface"
              )}
            >
              {renaming === game.id ? (
                <input
                  ref={renameInputRef}
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={() => commitRename(game.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.currentTarget.blur(); }
                    if (e.key === "Escape") { setRenaming(null); }
                  }}
                  className="w-full truncate rounded border border-border-strong bg-surface px-1.5 py-0.5 text-[13px] font-medium text-foreground focus:outline-none focus:ring-1 focus:ring-brand"
                  maxLength={255}
                />
              ) : (
                <button
                  onClick={() => startRename(game)}
                  className="min-w-0 w-full text-left"
                  title="Click to rename"
                >
                  <span className="block truncate text-[13px] font-medium text-foreground group-hover:text-brand transition-colors">
                    {game.title}
                  </span>
                </button>
              )}

              <span className="text-right text-[11px] text-muted tabular-nums">
                {new Date(game.created_at).toLocaleDateString(undefined, {
                  month: "short",
                  day: "numeric",
                })}
              </span>

              <div className="flex items-center justify-center gap-1.5">
                <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", STATUS_DOT[game.status])} />
                <span className="text-[11px] text-muted">{STATUS_LABEL[game.status]}</span>
              </div>

              <span className="text-right text-[11px] text-muted tabular-nums">
                {game.status === "ready" && game.clip_count != null ? game.clip_count : "—"}
              </span>

              <div className="flex items-center justify-end gap-1">
                <button
                  onClick={() => handleDelete(game.id, game.title)}
                  disabled={deleting === game.id}
                  className="opacity-0 group-hover:opacity-100 flex items-center justify-center h-6 w-6 rounded text-subtle hover:text-red-400 hover:bg-red-500/10 transition-all disabled:opacity-30"
                  title="Delete game"
                >
                  <Trash2 size={12} />
                </button>
                <Link
                  href={`/games/${game.id}`}
                  className="flex items-center justify-center h-6 w-6 rounded text-subtle hover:text-foreground hover:bg-surface-hover transition-colors"
                >
                  <ArrowRight size={12} />
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function GamesPage() {
  return (
    <RequireAuth>
      <GamesContent />
    </RequireAuth>
  );
}
