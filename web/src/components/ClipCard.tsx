"use client";

import { useState } from "react";
import { Play, User, Check, Bookmark, Download } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { type Clip, type ActionType, getClipDownloadUrl } from "@/lib/api";
import { startCrossOriginDownload } from "@/lib/download";
import { cn } from "@/lib/utils";
import { DemoBadge } from "@/components/DemoGame";

// The card carries the triage actions — Download and Save, done while scanning
// a grid. Label, Trim and the player tag are edits to a clip you are watching
// and live in ClipModal (CF-272): this row clipped them off its right edge.
// So the card holds no copy of the clip's state either; it renders the row the
// page has, which the modal's edits update.
interface ClipCardProps {
  clip: Clip;
  onPlay: (clip: Clip) => void;
  selected?: boolean;
  onToggleSelect?: (clipId: string) => void;
  onSave?: (clipId: string) => void;
}

export function ClipCard({ clip, onPlay, selected, onToggleSelect, onSave }: ClipCardProps) {
  const [downloadLoading, setDownloadLoading] = useState(false);

  async function handleDownload() {
    // Serialises the presign call — no more than that. It is released as soon as
    // startCrossOriginDownload appends the frame, so two clicks far enough
    // apart still start two transfers of the same clip and Chrome still saves
    // the second as `… (1).mp4`. Holding it for the transfer is not available:
    // the frame is cross-origin, so there is no event that says the download
    // finished, and a timer long enough to cover a slow one would leave the
    // button dead after every fast one.
    if (downloadLoading) return;
    setDownloadLoading(true);
    try {
      const { url } = await getClipDownloadUrl(clip.id);
      // Not window.location: see lib/download.ts — a rejected presigned GET
      // would render R2's XML error in place of the app.
      startCrossOriginDownload(url);
    } catch {
      alert("Could not prepare the download.");
    } finally {
      setDownloadLoading(false);
    }
  }

  const labels = clip.labels ?? [];
  const confidencePct = Math.round(clip.confidence * 100);
  const isDiscarded = labels.includes("not_an_action") || (clip.action_type === "unknown" && clip.confidence === 0);
  const duration = clip.end_time - clip.start_time;
  const displayLabels = labels.filter((l) => l !== "not_an_action");

  return (
    <div className={cn(
      "group overflow-hidden rounded-xl border bg-surface transition-all duration-200",
      selected
        ? "border-brand/50 ring-1 ring-brand/20"
        : "border-border hover:border-border-strong",
      isDiscarded && "opacity-35"
    )}>
      {/* Thumbnail */}
      <div className="relative aspect-video bg-surface-high overflow-hidden">
        {/* Selection checkbox */}
        {onToggleSelect && (
          <button
            type="button"
            onClick={() => onToggleSelect(clip.id)}
            className={cn(
              "absolute top-2 right-2 z-10 flex h-7 w-7 items-center justify-center rounded border transition-all duration-150",
              selected
                ? "bg-brand border-brand text-on-brand"
                : "bg-black/40 border-white/30 text-transparent hover:border-white/60"
            )}
            aria-label={selected ? "Deselect" : "Select"}
          >
            <Check size={13} strokeWidth={3} />
          </button>
        )}

        {/* Thumbnail image */}
        {clip.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={clip.thumbnail_url}
            loading="lazy"
            alt={`${clip.action_type} clip`}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.04]"
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <Play size={22} className="text-subtle" />
          </div>
        )}

        {/* The open target covers the frame (CF-273). A sibling button rather
            than `role="button"` + tabIndex on this div: the selection checkbox
            above is a real <button>, and a role="button" wrapping it is the
            same nested-interactive defect in ARIA form — #307 tried that and
            reverted it. Being a real button is also what lets the clip modal's
            focus trap hand focus back here on close.

            It sits at z-0, below the checkbox (z-10) and below the collection
            page's Remove button (z-10, outside this card), and everything
            painted over it is `pointer-events-none` so clicks land here.

            In select mode it toggles, as the old div did, but only as a mouse
            convenience: out of the tab order and hidden from assistive tech,
            because the checkbox is the keyboard path. Its mousedown is
            cancelled there too, so a click cannot leave focus on a hidden,
            unnamed control. Outside select mode it is one tab stop per card,
            deliberately.

            Opening focuses the button before playing. Chrome and Firefox
            already focus a clicked button, but WebKit (Safari, iOS) does not,
            and without this a mouse or tap open there leaves the modal's trap
            holding <body>, with nothing to hand focus back to on close. A
            programmatic focus after a pointer event does not match
            :focus-visible, so mouse users get no ring from it. */}
        <button
          type="button"
          onMouseDown={onToggleSelect ? (e) => e.preventDefault() : undefined}
          onClick={(e) => {
            if (onToggleSelect) {
              onToggleSelect(clip.id);
              return;
            }
            e.currentTarget.focus();
            onPlay(clip);
          }}
          aria-label={onToggleSelect ? undefined : `Play ${clip.action_type} clip`}
          tabIndex={onToggleSelect ? -1 : undefined}
          aria-hidden={onToggleSelect ? true : undefined}
          className="peer/play absolute inset-0 z-0 cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand"
        />

        {/* Play overlay. Keyboard focus on the play button reveals it the way
            hover does — keyed to that button (peer), not group-focus-within,
            which would also light it up for the checkbox and the footer's
            Download and Save buttons, none of which play anything. */}
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/35 peer-focus-visible/play:bg-black/35 peer-focus-visible/play:*:opacity-100 peer-focus-visible/play:*:scale-100 transition-colors duration-200">
          <div className="hover-reveal opacity-0 group-hover:opacity-100 scale-90 group-hover:scale-100 transition-all duration-200">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-white/15 backdrop-blur-sm border border-white/20">
              <Play size={16} className="text-white fill-white ml-0.5" />
            </div>
          </div>
        </div>

        {/* Action badges */}
        <div className="pointer-events-none absolute top-2 left-2 flex flex-wrap gap-1">
          {isDiscarded ? (
            <Badge label="removed" action="unknown" />
          ) : displayLabels.length > 0 ? (
            displayLabels.map((l) => (
              <Badge key={l} label={l} action={l as ActionType} />
            ))
          ) : (
            <Badge label={clip.action_type} action={clip.action_type} />
          )}
        </div>

        {/* Duration */}
        <span className="pointer-events-none absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white/90 tabular-nums backdrop-blur-sm">
          {formatDuration(duration)}
        </span>
      </div>

      {/* Card footer */}
      <div className="px-2.5 py-2">
        <div className="flex items-center justify-between gap-1">
          {/* Left: confidence and timestamp. `flex-1` (basis 0) gives this
              column only the width the actions leave, rather than sharing the
              squeeze with them. */}
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className={cn(
              "text-[11px] font-semibold tabular-nums",
              confidencePct >= 80 ? "text-emerald-400"
              : confidencePct >= 60 ? "text-amber-400"
              : "text-muted"
            )}>
              {confidencePct}%
            </span>
            <span className="shrink-0 text-[10px] text-subtle tabular-nums">{formatTimestamp(clip.start_time)}</span>
          </div>

          {/* Right: action buttons. No `shrink-0`: held at max-content width
              it could not wrap, and the card's overflow-hidden clipped
              whatever did not fit (CF-272). */}
          <div className="flex flex-wrap items-center justify-end gap-0.5">
            {/* Demo clips (CF-565): the api refuses download for them with
                409, so it is not offered rather than ending in an error. The
                badge says why, where no game banner is in view (a
                collection). */}
            {clip.is_sample && (
              <span title="Demo clip: download, trim and posting are for your own games">
                <DemoBadge className="mr-1" />
              </span>
            )}
            {!clip.is_sample && (
            <button
              onClick={handleDownload}
              disabled={downloadLoading}
              aria-busy={downloadLoading}
              className="flex min-h-8 items-center gap-1 rounded px-2 py-1.5 text-[10px] text-subtle hover:text-muted hover:bg-surface-hover transition-colors disabled:opacity-50"
              title="Download this clip"
            >
              <Download size={9} />
              Download
            </button>
            )}

            {onSave && (
              <button
                onClick={() => onSave(clip.id)}
                className="flex min-h-8 items-center gap-1 rounded px-2 py-1.5 text-[10px] text-subtle hover:text-muted hover:bg-surface-hover transition-colors"
                title="Save to collection"
              >
                <Bookmark size={9} />
                Save
              </button>
            )}
          </div>
        </div>

        {/* The tagged player, read-only — tagging is in the clip modal
            (CF-272). A line of its own under the row, not a third item in it:
            in a four-column grid at 1280 and 1440 the confidence, timestamp
            and the two actions take the whole row, and a name squeezed in
            beside them got 0px and showed only its icon. Here it has the
            card's width and truncates only when it is longer than that. */}
        {clip.player_name && (
          <div className="mt-0.5 flex min-w-0 items-center gap-1 text-[10px] text-muted" title={clip.player_name}>
            <User size={9} className="shrink-0" />
            <span className="truncate">{clip.player_name}</span>
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
