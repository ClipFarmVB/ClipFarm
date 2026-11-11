"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle, ArrowLeft, CheckSquare, Download, Scissors, Square, Trash2, X } from "lucide-react";
import { ClipCardSkeleton } from "@/components/ui/Skeleton";
import { ClipCard } from "@/components/ClipCard";
import { ClipModal, mergeClipFields } from "@/components/ClipModal";
import { CollectionPickerModal } from "@/components/CollectionPickerModal";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { getGame, getClips, getPlayers, deleteClips, type Game, type Clip, type Player, type ActionType, type ClipFilters } from "@/lib/api";
import { ApiError } from "@/lib/apiError";
import { estimateEtaSeconds, formatEta, pushSample, type ProgressSample } from "@/lib/eta";
import { cn } from "@/lib/utils";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import { DemoBadge, DemoBanner } from "@/components/DemoGame";

const ACTION_TYPES: ActionType[] = ["spike", "serve", "dig", "set", "block"];

// `#7 Alice`, or `Alice` for a player with no jersey number — the same form
// ClipCard's tag dropdown uses, so a player reads the same in both places.
function playerLabel(p: Player): string {
  return `${p.jersey_number != null ? `#${p.jersey_number} ` : ""}${p.name}`;
}

// How long the filters must be still before the clips are refetched. The
// sliders change on every step of a drag, and GET /games/{id}/clips is rate
// limited per caller (CF-186), so fetching per step could spend the whole
// budget in one gesture. Each settle costs one request per page of clips
// (CLIP_PAGE_SIZE), not one in all.
const CLIP_FILTER_DEBOUNCE_MS = 300;

// The grid loads every clip the filters admit by paging the route (CF-300):
// left to its default the route returns the first 50, and the page has no
// paging control, so a longer game's later clips were unreachable. 100 is the
// route's `page_size` maximum. The page cap bounds what one settle can spend
// of the 60/min budget; past 1,000 clips the grid shows the first 1,000.
const CLIP_PAGE_SIZE = 100;
const CLIP_PAGE_LIMIT = 10;

// Consecutive failed poll ticks before the processing view says it cannot
// reach the server. One or two are a blip the next tick usually rides out; at
// the 5s period this is 15s without an answer.
const POLL_TROUBLE_AFTER = 3;

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
  // Not expected here: UploadZone navigates to this page only after
  // completeUpload resolves, by which point the game is `queued`. Reachable
  // only by a typed URL: mid-transfer, or for an abandoned upload (a tab
  // closed mid-transfer), whose row stays `uploading` until the api's sweep
  // removes it — 24h old at the earliest, and only on that owner's next
  // upload, so possibly never. The entry keeps this map total over
  // Game["status"]; the page does not poll an `uploading` game, so such a
  // visit shows a static header for as long as the row lasts.
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
  // A clips request is in flight. Distinct from `loading`, which covers the
  // first fetch only: a refetch keeps the grid it has, so this flag is what
  // stops the empty state describing a result that has not landed yet.
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  // The last POLL_TROUBLE_AFTER poll ticks failed. The poll keeps going, so
  // this clears on the next tick that lands.
  const [pollTrouble, setPollTrouble] = useState(false);
  const [activeClipIndex, setActiveClipIndex] = useState<number | null>(null);
  const [filters, setFilters] = useState<ClipFilters>({ min_confidence: 0, min_score: 0, sort: "time" });
  // What the clips fetch reads. The controls update `filters` at once so the
  // numbers track the drag; the request waits for them to settle.
  const settledFilters = useDebouncedValue(filters, CLIP_FILTER_DEBOUNCE_MS);
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
      const { deleted } = await deleteClips(Array.from(selectedIds));
      setClips((prev) => prev.filter((c) => !selectedIds.has(c.id)));
      // The header's count comes from the game fetch, which a ready game
      // never repeats; without this it kept the pre-delete number until a
      // reload (CF-300). The api deletes the whole selection or refuses it,
      // so `deleted` equals what was asked for.
      setGame((g) => (g && g.clip_count != null
        ? { ...g, clip_count: Math.max(0, g.clip_count - deleted) }
        : g));
      exitSelectMode();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  }

  // Fetch game — poll if active
  useEffect(() => {
    // Set by the cleanup. The first fetch can resolve after the page has gone
    // (a fast navigation), and the interval it would then start is one the
    // cleanup never saw, so it polled for the rest of the session (CF-300).
    let disposed = false;
    let interval: ReturnType<typeof setInterval> | null = null;

    function stopPolling() {
      if (interval) clearInterval(interval);
      interval = null;
    }

    // A 404 is a game that does not exist or is not visible to this viewer
    // (`get_game`); anything else is a fault (CF-423).
    function showFailure(e: unknown, fallback: string) {
      if (e instanceof ApiError && e.status === 404) setNotFound(true);
      else setError(e instanceof Error ? e.message : fallback);
    }

    async function fetchGame() {
      try {
        const g = await getGame(id);
        if (disposed) return;
        setGame(g);
        if (g.status === "processing" || g.status === "queued") {
          let failedTicks = 0;
          interval = setInterval(async () => {
            // Ticks do not wait for each other, so one can land after the
            // poll has stopped (a slower tick behind the one that saw
            // `ready`); a late answer then must not touch the page.
            try {
              const updated = await getGame(id);
              if (disposed || interval === null) return;
              failedTicks = 0;
              setPollTrouble(false);
              setGame(updated);
              if (updated.status === "ready" || updated.status === "failed") stopPolling();
            } catch (e) {
              if (disposed || interval === null) return;
              // A 404 is final: the game was deleted, say in another tab
              // (CF-300). Anything else (a network blip, a 502 during a
              // deploy, a 429) keeps the processing view and the poll, so
              // the next tick can recover. CF-410 (#533) reads a 429's
              // Retry-After here.
              if (e instanceof ApiError && e.status === 404) {
                stopPolling();
                showFailure(e, "Failed to refresh game.");
                return;
              }
              failedTicks += 1;
              if (failedTicks >= POLL_TROUBLE_AFTER) setPollTrouble(true);
            }
          }, 5000);
        }
      } catch (e) {
        if (disposed) return;
        showFailure(e, "Failed to load game.");
      }
    }

    fetchGame();
    return () => {
      disposed = true;
      stopPolling();
    };
  }, [id]);

  // Keyed on readiness, not on `game`: the poll and a delete both replace
  // `game`, and neither changes which clips the filters admit.
  const gameReady = game?.status === "ready";

  // Fetch clips when game is ready, every page of them (CLIP_PAGE_SIZE).
  useEffect(() => {
    if (!gameReady) return;
    // A newer load (the filters settled again) supersedes this one: it must
    // neither set the grid nor clear `fetching` under the newer request, or a
    // slow older response could replace the newer grid or flash the empty
    // state.
    let cancelled = false;
    setFetching(true);
    (async () => {
      try {
        const loaded: Clip[] = [];
        const seen = new Set<string>();
        for (let page = 1; page <= CLIP_PAGE_LIMIT; page++) {
          if (cancelled) return;
          const batch = await getClips(id, { ...settledFilters, page, page_size: CLIP_PAGE_SIZE });
          // The route pages by offset over an order with no id tiebreak, so
          // a clip can straddle two pages; keep its first appearance.
          for (const c of batch) {
            if (seen.has(c.id)) continue;
            seen.add(c.id);
            loaded.push(c);
          }
          if (batch.length < CLIP_PAGE_SIZE) break;
          if (page === CLIP_PAGE_LIMIT) {
            // A full last page may also be the last clip. One single-clip
            // probe at offset CLIP_PAGE_LIMIT * CLIP_PAGE_SIZE tells which,
            // and only a game this large pays for it. The probe only decides
            // a warning, so its failure leaves the grid as loaded.
            try {
              const probe = await getClips(id, {
                ...settledFilters,
                page: CLIP_PAGE_LIMIT * CLIP_PAGE_SIZE + 1,
                page_size: 1,
              });
              if (probe.length > 0 && !cancelled) {
                console.warn(`Clip grid stopped at ${CLIP_PAGE_LIMIT} pages; later clips are not shown.`);
              }
            } catch (e) {
              console.warn("Could not check for clips past the grid's page cap.", e);
            }
          }
        }
        if (!cancelled) setClips(loaded);
      } catch (e) {
        // A failure on any page fails the load, as one on the first page
        // always has: a grid quietly short of clips is the defect this
        // paging exists to fix.
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load clips.");
      } finally {
        if (!cancelled) {
          setLoading(false);
          setFetching(false);
        }
      }
    })();
    return () => {
      cancelled = true;
      // A newer load sets this again in the same commit; with none (the
      // game stopped being ready) the skeleton must not wait on a load that
      // will never report.
      setFetching(false);
    };
  }, [gameReady, id, settledFilters]);

  // Fetch the roster once the game is ready (CF-250). Separate from the clips
  // fetch on purpose: the roster does not change with the filters, so it is
  // not refetched on every slider step, and a roster that fails to load costs
  // the player filter and the tag action's dropdown, not the grid — the clips
  // stand on their own.
  useEffect(() => {
    if (!gameReady) return;
    getPlayers()
      .then(setPlayers)
      .catch((e) => {
        console.warn("Roster failed to load; the player filter and tag dropdown are off.", e);
        setPlayers([]);
      });
  }, [id, gameReady]);

  // Whether anything besides the player filter narrows the grid, for the
  // empty state's copy. Any non-empty `action_type` list counts: the API
  // applies it as given, and even all five badges exclude `unknown`-typed
  // clips (the `removed` ones), which can carry a player tag too.
  const otherFiltersNarrow = Boolean(
    settledFilters.action_type?.length ||
    settledFilters.min_confidence ||
    settledFilters.min_score
  );

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

  if (notFound) {
    return (
      <div className="fade-up flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-20 text-center">
        <Scissors size={28} className="text-subtle mb-3" />
        <h1 className="text-[13px] font-medium text-foreground">Game not found</h1>
        <p className="mt-1 text-[12px] text-muted">
          It may have been deleted, or the link is wrong.
        </p>
        <Link href="/games" className="mt-4">
          <Button variant="secondary" size="sm">Back to library</Button>
        </Link>
      </div>
    );
  }

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
            <div className="flex min-w-0 items-center gap-2">
              <h1 className="text-[18px] font-semibold text-foreground tracking-tight truncate">
                {game.title}
              </h1>
              {game.is_sample && <DemoBadge />}
            </div>
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

      {game.is_sample && <DemoBanner credit={game.sample_credit} />}

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
          {pollTrouble && (
            <p role="status" className="mt-4 text-[12px] text-red-400 max-w-xs">
              Can&apos;t reach ClipFarm right now. Still trying; check your connection if this stays.
            </p>
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
              No condensed video for this game. Either there wasn&apos;t enough downtime
              worth cutting, the ball couldn&apos;t be tracked reliably enough to tell
              rallies from downtime — in which case the full video is kept rather than
              risk cutting play — or something went wrong while generating it. Your
              clips and the original are unaffected.
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
              {/* Player filter (CF-250). Only once there is a roster to pick
                  from: with no players the control could offer nothing but
                  its own clear option. "All players" is that explicit clear —
                  most clips are untagged, and a filter you cannot leave is
                  worse than none. */}
              {players.length > 0 && (
                <select
                  aria-label="Filter by player"
                  value={filters.player_id ?? ""}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, player_id: e.target.value || undefined }))
                  }
                  className={cn(
                    "max-w-[10rem] truncate rounded-md border bg-surface px-2.5 py-2 text-[11px] font-medium transition-all duration-150 sm:py-1",
                    filters.player_id
                      ? "border-brand/30 bg-brand/5 text-brand"
                      : "border-border text-muted hover:border-border-strong hover:text-foreground"
                  )}
                >
                  <option value="">All players</option>
                  {players.map((p) => (
                    <option key={p.id} value={p.id}>{playerLabel(p)}</option>
                  ))}
                </select>
              )}

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

          {/* Loading clips skeletons: the first fetch, and a refetch with no
              grid to keep on screen (the empty state's "Show all players",
              or one empty player to another). */}
          {(loading || (fetching && clips.length === 0)) && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 stagger">
              {[...Array(8)].map((_, i) => <ClipCardSkeleton key={i} />)}
            </div>
          )}

          {/* Empty filtered state. Reads the settled filters, which are what
              the empty result answers to; the control shows the live ones.
              Never while a fetch is pending: `clips` would still be the old
              `[]` and the line would describe a result that is not in yet. */}
          {!loading && !fetching && clips.length === 0 && (
            settledFilters.player_id ? (
              <div className="flex flex-col items-center py-16 text-center">
                <p className="text-[13px] text-muted">
                  No clips tagged{" "}
                  <span className="font-medium text-foreground">
                    {players.find((p) => p.id === settledFilters.player_id)?.name ?? "this player"}
                  </span>
                  {otherFiltersNarrow ? " match your other filters." : " in this game."}
                </p>
                <button
                  onClick={() => setFilters((f) => ({ ...f, player_id: undefined }))}
                  className="mt-3 rounded-md border border-border px-2.5 py-2 text-[11px] font-medium text-muted hover:border-border-strong hover:text-foreground transition-all duration-150 sm:py-1"
                >
                  Show all players
                </button>
              </div>
            ) : (
              <p className="py-16 text-center text-[13px] text-muted">
                No clips match your filters.
              </p>
            )
          )}

          {/* Clip grid */}
          {!loading && clips.length > 0 && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 stagger">
              {clips.map((clip, i) => (
                <ClipCard
                  key={clip.id}
                  clip={clip}
                  onPlay={() => setActiveClipIndex(i)}
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
          // Merged onto the row as it is when the write lands, by id: a write
          // can settle after another on the same clip, or after ← →.
          onUpdate={(id, fields) => setClips((prev) => mergeClipFields(prev, id, fields))}
          // The roster turns on the modal's edit bar — Label, Trim and the
          // player tag, which used to sit on the card (CF-272).
          players={players}
          // How long the source is, so a trim past its end is expected to
          // come back cut short at it (CF-272).
          sourceDuration={game?.original_duration}
          // Every game reachable here is the viewer's own. `GET /games/{id}` is
          // visibility-scoped rather than owner-only, but no write path exists
          // for a game's visibility — `test_visibility_write_paths_are_declared`
          // holds that — so every game is private and only its owner can open
          // it. Revisit if a game setter ever lands; the collections page,
          // which really is cross-owner, passes nothing.
          ownsClip
        />
      )}
    </div>
  );
}
