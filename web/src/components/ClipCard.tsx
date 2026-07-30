"use client";

import { useState } from "react";
import { Play, User, ChevronLeft, ChevronRight, Tag, Check, Bookmark } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { type Clip, type Player, type ActionType, tagClip, updateClipLabels, trimClip } from "@/lib/api";
import { cn } from "@/lib/utils";

const LABEL_OPTIONS = ["spike", "serve", "dig", "set", "block", "not_an_action"];

interface ClipCardProps {
  clip: Clip;
  players: Player[];
  onPlay: (clip: Clip) => void;
  onUpdate?: (clip: Clip) => void;
  selected?: boolean;
  onToggleSelect?: (clipId: string) => void;
  onSave?: (clipId: string) => void;
}

export function ClipCard({ clip, players, onPlay, onUpdate, selected, onToggleSelect, onSave }: ClipCardProps) {
  const [tagging, setTagging] = useState(false);
  const [labeling, setLabeling] = useState(false);
  const [trimming, setTrimming] = useState(false);
  const [localPlayerName, setLocalPlayerName] = useState(clip.player_name);
  const [localAction, setLocalAction] = useState(clip.action_type);
  const [localConfidence, setLocalConfidence] = useState(clip.confidence);
  const [localLabels, setLocalLabels] = useState<string[]>(clip.labels ?? []);
  const [localStart, setLocalStart] = useState(clip.start_time);
  const [localEnd, setLocalEnd] = useState(clip.end_time);
  const [trimLoading, setTrimLoading] = useState(false);
  const [labelLoading, setLabelLoading] = useState(false);

  async function handleTag(playerId: string) {
    setTagging(false);
    const updated = await tagClip(clip.id, playerId);
    setLocalPlayerName(updated.player_name);
  }

  async function handleToggleLabel(label: string) {
    if (labelLoading) return;
    let next: string[];
    if (label === "not_an_action") {
      next = ["not_an_action"];
    } else {
      const without = localLabels.filter((l) => l !== "not_an_action");
      if (without.includes(label)) {
        next = without.filter((l) => l !== label);
      } else if (without.length >= 2) {
        next = [without[1], label];
      } else {
        next = [...without, label];
      }
    }
    if (next.length === 0) next = ["not_an_action"];

    const prev = localLabels;
    setLocalLabels(next);
    setLabelLoading(true);
    try {
      const updated = await updateClipLabels(clip.id, next);
      setLocalLabels(updated.labels);
      setLocalAction(updated.action_type);
      setLocalConfidence(updated.confidence);
      onUpdate?.(updated);
    } catch (err) {
      console.error("Label update failed:", err);
      setLocalLabels(prev);
    } finally {
      setLabelLoading(false);
    }
  }

  async function handleTrim(startDelta: number, endDelta: number) {
    setTrimLoading(true);
    try {
      const updated = await trimClip(clip.id, startDelta, endDelta);
      setLocalStart(updated.start_time);
      setLocalEnd(updated.end_time);
      onUpdate?.(updated);
    } catch (err) {
      console.error("Trim failed:", err);
    } finally {
      setTrimLoading(false);
    }
  }

  const confidencePct = Math.round(localConfidence * 100);
  const isDiscarded = localLabels.includes("not_an_action") || (localAction === "unknown" && localConfidence === 0);
  const duration = localEnd - localStart;
  const displayLabels = localLabels.filter((l) => l !== "not_an_action");

  return (
    <div className={cn(
      "group overflow-hidden rounded-xl border bg-surface transition-all duration-200",
      selected
        ? "border-brand/50 ring-1 ring-brand/20"
        : "border-border hover:border-border-strong",
      isDiscarded && "opacity-35"
    )}>
      {/* Thumbnail */}
      <div
        className="relative aspect-video cursor-pointer bg-surface-high overflow-hidden"
        onClick={() => {
          if (onToggleSelect) onToggleSelect(clip.id);
          else onPlay(clip);
        }}
      >
        {/* Selection checkbox */}
        {onToggleSelect && (
          <button
            onClick={(e) => { e.stopPropagation(); onToggleSelect(clip.id); }}
            className={cn(
              "absolute top-2 right-2 z-10 flex h-5 w-5 items-center justify-center rounded border transition-all duration-150",
              selected
                ? "bg-brand border-brand text-[#0c0c0e]"
                : "bg-black/40 border-white/30 text-transparent hover:border-white/60"
            )}
            aria-label={selected ? "Deselect" : "Select"}
          >
            <Check size={11} strokeWidth={3} />
          </button>
        )}

        {/* Thumbnail image */}
        {clip.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={clip.thumbnail_url}
            loading="lazy"
            alt={`${localAction} clip`}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.04]"
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <Play size={22} className="text-subtle" />
          </div>
        )}

        {/* Play overlay */}
        <div className="absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/35 transition-colors duration-200">
          <div className="opacity-0 group-hover:opacity-100 scale-90 group-hover:scale-100 transition-all duration-200">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-white/15 backdrop-blur-sm border border-white/20">
              <Play size={14} className="text-white fill-white ml-0.5" />
            </div>
          </div>
        </div>

        {/* Action badges */}
        <div className="absolute top-2 left-2 flex flex-wrap gap-1">
          {isDiscarded ? (
            <Badge label="removed" action="unknown" />
          ) : displayLabels.length > 0 ? (
            displayLabels.map((l) => (
              <Badge key={l} label={l} action={l as ActionType} />
            ))
          ) : (
            <Badge label={localAction} action={localAction as ActionType} />
          )}
        </div>

        {/* Duration */}
        <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white/90 tabular-nums backdrop-blur-sm">
          {formatDuration(duration)}
        </span>
      </div>

      {/* Card footer */}
      <div className="px-2.5 py-2">
        <div className="flex items-center justify-between gap-1">
          {/* Left: confidence + timestamp */}
          <div className="flex items-center gap-2 min-w-0">
            <span className={cn(
              "text-[11px] font-semibold tabular-nums",
              confidencePct >= 80 ? "text-emerald-400"
              : confidencePct >= 60 ? "text-amber-400"
              : "text-muted"
            )}>
              {confidencePct}%
            </span>
            <span className="text-[10px] text-subtle truncate">{formatTimestamp(localStart)}</span>
          </div>

          {/* Right: action buttons */}
          <div className="flex items-center gap-0.5 shrink-0">
            <button
              onClick={() => {
                if (!labeling && localLabels.length === 0 && localAction !== "unknown") {
                  setLocalLabels([localAction]);
                }
                setLabeling(!labeling);
                setTrimming(false);
              }}
              className={cn(
                "flex items-center gap-1 rounded px-1.5 py-1 text-[10px] font-medium transition-colors",
                labeling ? "text-brand bg-brand/8" : "text-subtle hover:text-muted hover:bg-surface-hover"
              )}
            >
              <Tag size={9} />
              Label
            </button>

            {onSave && (
              <button
                onClick={() => onSave(clip.id)}
                className="flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-subtle hover:text-muted hover:bg-surface-hover transition-colors"
                title="Save to collection"
              >
                <Bookmark size={9} />
                Save
              </button>
            )}

            <button
              onClick={() => { setTrimming(!trimming); setLabeling(false); }}
              className={cn(
                "flex items-center gap-0.5 rounded px-1.5 py-1 text-[10px] font-medium transition-colors",
                trimming ? "text-brand bg-brand/8" : "text-subtle hover:text-muted hover:bg-surface-hover"
              )}
            >
              <ChevronLeft size={9} /><ChevronRight size={9} className="-ml-1" />
              Trim
            </button>

            {/* Player tag */}
            {tagging ? (
              <select
                autoFocus
                className="rounded border border-border bg-surface-high px-1.5 py-0.5 text-[10px] text-foreground focus:outline-none"
                onBlur={() => setTagging(false)}
                onChange={(e) => handleTag(e.target.value)}
                defaultValue=""
              >
                <option value="" disabled>Player…</option>
                {players.map((p) => (
                  <option key={p.id} value={p.id}>
                    #{p.jersey_number} {p.name}
                  </option>
                ))}
              </select>
            ) : (
              <button
                onClick={() => setTagging(true)}
                className="flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-subtle hover:text-muted hover:bg-surface-hover transition-colors"
              >
                <User size={9} />
                {localPlayerName ?? "Tag"}
              </button>
            )}
          </div>
        </div>

        {/* Label panel */}
        {labeling && (
          <div className="mt-2 flex flex-wrap gap-1 pt-2 border-t border-border">
            {LABEL_OPTIONS.map((label) => {
              const isNotAction = label === "not_an_action";
              const active = isNotAction
                ? localLabels.includes("not_an_action") || isDiscarded
                : localLabels.includes(label);
              return (
                <button
                  key={label}
                  disabled={labelLoading}
                  onClick={() => handleToggleLabel(label)}
                  className={cn(
                    "rounded px-2 py-0.5 text-[10px] font-medium capitalize transition-all duration-100 disabled:opacity-40",
                    active && isNotAction
                      ? "bg-red-500/10 text-red-400 ring-1 ring-red-500/20"
                      : active
                      ? "bg-brand/10 text-brand ring-1 ring-brand/20"
                      : "bg-surface-high text-subtle hover:text-muted"
                  )}
                >
                  {isNotAction ? "Not an action" : label}
                </button>
              );
            })}
          </div>
        )}

        {/* Trim panel */}
        {trimming && (
          <div className="mt-2 pt-2 border-t border-border">
            <div className="flex items-center justify-between gap-2">
              {/* Start controls */}
              <div className="flex flex-col items-center gap-1">
                <span className="text-[9px] font-semibold uppercase tracking-widest text-subtle">Start</span>
                <div className="flex items-center gap-1">
                  {[{ label: "−2s", d: [-2, 0] }, { label: "+2s", d: [2, 0] }].map(({ label, d }) => (
                    <button
                      key={label}
                      disabled={trimLoading}
                      onClick={() => handleTrim(d[0], d[1])}
                      className="rounded bg-surface-high px-1.5 py-0.5 text-[10px] font-medium text-subtle hover:text-foreground hover:bg-surface-hover transition-colors disabled:opacity-40"
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <span className="text-[10px] text-muted tabular-nums">{formatTimestamp(localStart)}</span>
              </div>

              <span className="text-[10px] text-subtle tabular-nums">{formatDuration(duration)}</span>

              {/* End controls */}
              <div className="flex flex-col items-center gap-1">
                <span className="text-[9px] font-semibold uppercase tracking-widest text-subtle">End</span>
                <div className="flex items-center gap-1">
                  {[{ label: "−2s", d: [0, -2] }, { label: "+2s", d: [0, 2] }].map(({ label, d }) => (
                    <button
                      key={label}
                      disabled={trimLoading}
                      onClick={() => handleTrim(d[0], d[1])}
                      className="rounded bg-surface-high px-1.5 py-0.5 text-[10px] font-medium text-subtle hover:text-foreground hover:bg-surface-hover transition-colors disabled:opacity-40"
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <span className="text-[10px] text-muted tabular-nums">{formatTimestamp(localEnd)}</span>
              </div>
            </div>

            {trimLoading && (
              <p className="mt-1.5 text-center text-[10px] text-brand animate-pulse">Re-cutting clip…</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function formatDuration(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function formatTimestamp(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}
