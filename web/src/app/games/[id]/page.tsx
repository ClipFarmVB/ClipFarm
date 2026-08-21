"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle, ArrowLeft, CheckSquare, Download, Scissors, Square, Trash2, X } from "lucide-react";
import { ClipCardSkeleton } from "@/components/ui/Skeleton";
import { ClipCard } from "@/components/ClipCard";
import { ClipModal } from "@/components/ClipModal";
import { CollectionPickerModal } from "@/components/CollectionPickerModal";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { getGame, getClips, getPlayers, deleteClips, type Game, type Clip, type Player, type ActionType, type ClipFilters } from "@/lib/api";
import { estimateEtaSeconds, formatEta, pushSample, type ProgressSample } from "@/lib/eta";
import { cn } from "@/lib/utils";

const ACTION_TYPES: ActionType[] = ["spike", "serve", "dig", "set", "block"];

function fmtDuration(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}

const STATUS_STYLES: Record<Game["status"], string> = {
  // Reachable only by the tab performing the upload — the api hands back an
  // `uploading` game while its video is still transferring to R2.
  uploading:  "text-zinc-500 bg-zinc-500/8 border-zinc-500/20",
  queued:     "text-zinc-500 bg-zinc-500/8 border-zinc-500/20",
  processing: "text-blue-400 bg-blue-500/8 border-blue-500/20",
  ready:      "text-emerald-400 bg-emerald-500/8 border-emerald-500/20",
  failed:     "text-red-400 bg-red-500/8 border-red-500/20",
};

// Worker-reported stage slugs → display text. Unknown slugs (e.g. from a
// newer backend) fall back to the generic label.
const STAGE_LABELS: Record<string, string> = {
  downloading:        "Preparing video",
  analyzing_audio:    "Analyzing audio",
  tracking_ball:      "Tracking the ball",
  scoring_highlights: "Scoring highlights",
  refining_actions:   "Classifying actions",
  cutting_clips:      "Cutting clips",
  condensing:         "Building condensed video",
};

export default function GamePage() {
  const { id } = useParams<{ id: string }>();
  const [game, setGame] = useState<Game | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [players, setPlayers] = useState<Player[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeClipIndex, setActiveClipIndex] = useState<number | null>(null);
  const [filters, setFilters] = useState<ClipFilters>({ min_confidence: 0, min_score: 0, sort: "time" });
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [savingClipId, setSavingClipId] = useState<string | null>(null);
  // ETA from recent progress velocity: refs hold the sample window and the
  // EMA-smoothed seconds across polls; state holds the display string.
  const etaSamplesRef = useRef<ProgressSample[]>([]);
  const etaSecondsRef = useRef<number | null>(null);
  const etaStageRef = useRef<string | null>(null);
  const [etaText, setEtaText] = useState<string | null>(null);

  useEffect(() => {
    if (!game || game.status !== "processing") {
      etaSamplesRef.current = [];
      etaSecondsRef.current = null;
      etaStageRef.current = null;
      setEtaText(null);
      return;
    }
    const progress = game.progress ?? 0;
    const prev = etaSamplesRef.current[etaSamplesRef.current.length - 1];
    // A stage change or a backwards jump (a retry re-entering "processing"
    // zeroes progress) invalidates the velocity window — the stale samples
    // would read as a huge fake spike or a negative velocity. Start over.
    if ((game.progress_stage ?? null) !== etaStageRef.current || (prev && progress < prev.progress)) {
      etaSamplesRef.current = [];
      etaSecondsRef.current = null;
    }
    etaStageRef.current = game.progress_stage ?? null;
    etaSamplesRef.current = pushSample(etaSamplesRef.current, Date.now(), progress);
    const eta = estimateEtaSeconds(etaSamplesRef.current, etaSecondsRef.current);
    etaSecondsRef.current = eta;
    setEtaText(eta === null ? null : formatEta(eta));
  }, [game]);

  const toggleSelect = useCallback((clipId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(clipId)) next.delete(clipId);
      else next.add(clipId);
      return next;
    });
  }, []);

  function exitSelectMode() {
    setSelectMode(false);
    setSelectedIds(new Set());
  }

  async function handleDelete() {
    if (selectedIds.size === 0) return;
    if (!confirm(`Delete ${selectedIds.size} clip${selectedIds.size === 1 ? "" : "s"}? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await deleteClips(Array.from(selectedIds));
      setClips((prev) => prev.filter((c) => !selectedIds.has(c.id)));
      exitSelectMode();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  }

  // Fetch game — poll if active
  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | null = null;

    async function fetchGame() {
      try {
        const g = await getGame(id);
        setGame(g);
        if (g.status === "processing" || g.status === "queued") {
          interval = setInterval(async () => {
            const updated = await getGame(id);
            setGame(updated);
            if (updated.status === "ready" || updated.status === "failed") {
              if (interval) clearInterval(interval);
            }
          }, 5000);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load game.");
      }
    }

    fetchGame();
    return () => { if (interval) clearInterval(interval); };
  }, [id]);

  // Fetch clips when game is ready
  useEffect(() => {
    if (!game || game.status !== "ready") return;
    Promise.all([getClips(id, filters), getPlayers()])
      .then(([c, p]) => { setClips(c); setPlayers(p); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [game, id, filters]);

  const toggleActionFilter = useCallback((action: ActionType) => {
    setFilters((f) => {
      const current = f.action_type ?? [];
      return {
        ...f,
        action_type: current.includes(action)
          ? current.filter((a) => a !== action)
          : [...current, action],
      };
    });
  }, []);

  if (error) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2.5 text-[12px] text-red-400">
        <AlertCircle size={13} className="shrink-0" /> {error}
      </div>
    );
  }

  // Loading state — game not yet fetched
  if (!game) {
    return (
      <div className="fade-up">
        <div className="mb-6 flex items-center justify-between">
          <div className="skeleton h-5 w-48 rounded" />
          <div className="skeleton h-5 w-16 rounded" />
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
          {[...Array(8)].map((_, i) => <ClipCardSkeleton key={i} />)}
        </div>
      </div>
    );
  }

  return (
    <div className="fade-up">
      {/* Header */}
      <div className="mb-6">
        <Link
          href="/games"
          className="mb-3 inline-flex items-center gap-1 text-[11px] text-muted hover:text-foreground transition-colors"
        >
          <ArrowLeft size={11} />
          Library
        </Link>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-[18px] font-semibold text-foreground tracking-tight truncate">
              {game.title}
            </h1>
            <p className="mt-0.5 text-[12px] text-muted">
              {game.clip_count ?? clips.length} clip{(game.clip_count ?? clips.length) !== 1 ? "s" : ""}
            </p>
          </div>
          <span className={cn(
            "shrink-0 rounded-md border px-2 py-0.5 text-[11px] font-medium",
            STATUS_STYLES[game.status]
          )}>
            {game.status.charAt(0).toUpperCase() + game.status.slice(1)}
          </span>
        </div>
      </div>

      {/* Processing state */}
      {(game.status === "queued" || game.status === "processing") && (
        <div className="flex flex-col items-center justify-center rounded-lg border border-border bg-surface py-20 text-center">
          <div className="mb-4 h-8 w-8 rounded-full border-2 border-border-strong border-t-brand animate-spin" />
          <p className="text-[13px] font-medium text-foreground">
            {game.status === "queued"
              ? "Queued for processing"
              : STAGE_LABELS[game.progress_stage ?? ""] ?? "Processing"}
          </p>
          {game.status === "processing" && (
            <div className="mt-4 w-full max-w-xs px-4">
              <div className="flex justify-between text-[11px] text-muted mb-1.5">
                <span>{etaText ?? ((game.progress ?? 0) >= 0.99 ? "Finishing up…" : "Estimating time…")}</span>
                <span className="tabular-nums">{Math.round((game.progress ?? 0) * 100)}%</span>
              </div>
              <div className="h-0.5 rounded-full bg-surface-high overflow-hidden">
                <div
                  className="h-full rounded-full bg-brand transition-all duration-500 ease-out"
                  style={{ width: `${Math.round((game.progress ?? 0) * 100)}%` }}
                />
              </div>
            </div>
          )}
          <p className="mt-4 text-[12px] text-muted max-w-xs">
            Detecting actions and cutting clips. You can leave this page — we&apos;ll keep working.
          </p>
        </div>
      )}

      {game.status === "failed" && (
        <div className="flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/5 px-4 py-3 text-[13px] text-red-400">
          <AlertCircle size={14} className="shrink-0" />
          Processing failed. Try re-uploading the game.
        </div>
      )}

      {game.status === "ready" && (
        <>
          {/* Condensed (dead-time-removed) video */}
          {game.condensed_video_url && (
            <div className="mb-5 rounded-lg border border-border bg-surface p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Scissors size={13} className="text-brand" />
                  <h2 className="text-[13px] font-semibold text-foreground">Dead time removed</h2>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  {game.original_duration != null && game.original_duration > 0 && game.condensed_duration != null && (
                    <span className="text-[11px] text-muted tabular-nums">
                      {fmtDuration(game.original_duration)} → {fmtDuration(game.condensed_duration)}
                      {" · "}
                      {Math.round((1 - game.condensed_duration / game.original_duration) * 100)}% removed
                    </span>
                  )}
                  {/* Filename comes from the presigned URL's Content-Disposition;
                      the download attribute is ignored cross-origin. */}
                  <a
                    href={game.condensed_video_url}
                    className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-2 text-[11px] font-medium text-muted hover:border-border-strong hover:text-foreground transition-all duration-150 sm:py-1"
                  >
                    <Download size={11} />
                    Download
                  </a>
                </div>
              </div>
              <video
                controls
                preload="metadata"
                src={game.condensed_video_url}
                className="w-full rounded-md bg-black"
              />
            </div>
          )}
          {game.condense_requested && !game.condensed_video_url && (
            <p className="mb-5 rounded-md border border-border bg-surface px-3 py-2.5 text-[12px] text-muted">
              A condensed video was requested but couldn&apos;t be generated for this game.
            </p>
          )}

          {/* Filter bar */}
          <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2.5">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-subtle mr-1">Filter</span>
            {ACTION_TYPES.map((action) => {
              const active = !filters.action_type?.length || filters.action_type.includes(action);
              return (
                <button
                  key={action}
                  onClick={() => toggleActionFilter(action)}
                  className={cn(
                    "flex min-h-9 items-center py-1 transition-all duration-150 press sm:min-h-0 sm:py-0",
                    active ? "opacity-100" : "opacity-25 hover:opacity-50"
                  )}
                >
                  <Badge label={action} action={action} />
                </button>
              );
            })}

            <div className="flex w-full flex-wrap items-center gap-x-3 gap-y-2 sm:ml-auto sm:w-auto sm:flex-nowrap">
              {/* Sort toggle: chronological vs highlight score */}
              <button
                onClick={() =>
                  setFilters((f) => ({ ...f, sort: f.sort === "score" ? "time" : "score" }))
                }
                className={cn(
                  "flex items-center gap-1.5 rounded-md border px-2.5 py-2 text-[11px] font-medium transition-all duration-150 sm:py-1",
                  filters.sort === "score"
                    ? "border-brand/30 bg-brand/5 text-brand"
                    : "border-border text-muted hover:border-border-strong hover:text-foreground"
                )}
              >
                {filters.sort === "score" ? "Top plays" : "Timeline"}
              </button>

              {/* Highlight score slider */}
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-muted">Highlights</span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={(filters.min_score ?? 0) * 100}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, min_score: Number(e.target.value) / 100 }))
                  }
                  className="w-24 sm:w-20"
                />
                <span className="text-[10px] text-muted tabular-nums w-6 text-right">
                  {Math.round((filters.min_score ?? 0) * 100)}%
                </span>
              </div>

              {/* Confidence slider */}
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-muted">Confidence</span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={(filters.min_confidence ?? 0) * 100}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, min_confidence: Number(e.target.value) / 100 }))
                  }
                  className="w-24 sm:w-20"
                />
                <span className="text-[10px] text-muted tabular-nums w-6 text-right">
                  {Math.round((filters.min_confidence ?? 0) * 100)}%
                </span>
              </div>

              {/* Select toggle */}
              <button
                onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
                className={cn(
                  "ml-auto flex items-center gap-1.5 rounded-md border px-2.5 py-2 text-[11px] font-medium transition-all duration-150 sm:ml-0 sm:py-1",
                  selectMode
                    ? "border-brand/30 bg-brand/5 text-brand"
                    : "border-border text-muted hover:border-border-strong hover:text-foreground"
                )}
              >
                <CheckSquare size={11} />
                {selectMode ? "Cancel" : "Select"}
              </button>
            </div>
          </div>

          {/* Selection action bar */}
          {selectMode && (
            <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-border-strong bg-surface-high px-3 py-2">
              <span className="text-[12px] font-medium text-foreground">
                {selectedIds.size} selected
              </span>
              <button
                onClick={() => setSelectedIds(new Set(clips.map((c) => c.id)))}
                className="flex items-center gap-1 rounded px-2 py-1.5 text-[11px] text-muted hover:text-foreground hover:bg-surface-hover transition-colors"
              >
                <CheckSquare size={11} />
                All ({clips.length})
              </button>
              <button
                onClick={() => setSelectedIds(new Set())}
                disabled={selectedIds.size === 0}
                className="flex items-center gap-1 rounded px-2 py-1.5 text-[11px] text-muted hover:text-foreground hover:bg-surface-hover transition-colors disabled:opacity-30"
              >
                <Square size={11} />
                Clear
              </button>
              <div className="ml-auto flex items-center gap-2">
                <Button
                  size="sm"
                  variant="danger"
                  onClick={handleDelete}
                  disabled={selectedIds.size === 0 || deleting}
                >
                  <Trash2 size={11} />
                  {deleting ? "Deleting…" : `Delete ${selectedIds.size || ""}`}
                </Button>
                <button
                  onClick={exitSelectMode}
                  aria-label="Exit select mode"
                  className="flex items-center justify-center h-8 w-8 rounded text-subtle hover:text-foreground hover:bg-surface-hover transition-colors sm:h-6 sm:w-6"
                >
                  <X size={12} />
                </button>
              </div>
            </div>
          )}

          {/* Loading clips skeletons */}
          {loading && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 stagger">
              {[...Array(8)].map((_, i) => <ClipCardSkeleton key={i} />)}
            </div>
          )}

          {/* Empty filtered state */}
          {!loading && clips.length === 0 && (
            <p className="py-16 text-center text-[13px] text-muted">
              No clips match your filters.
            </p>
          )}

          {/* Clip grid */}
          {!loading && clips.length > 0 && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 stagger">
              {clips.map((clip, i) => (
                <ClipCard
                  key={clip.id}
                  clip={clip}
                  players={players}
                  onPlay={() => setActiveClipIndex(i)}
                  onUpdate={(updated) =>
                    setClips((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
                  }
                  selected={selectedIds.has(clip.id)}
                  onToggleSelect={selectMode ? toggleSelect : undefined}
                  onSave={selectMode ? undefined : (id) => setSavingClipId(id)}
                />
              ))}
            </div>
          )}
        </>
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
