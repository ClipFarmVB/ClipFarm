/**
 * A static, illustrative clip card for the landing page — not a real clip.
 *
 * The thumbnail is always dark, like the video frame it stands in for, so the
 * white badge and glyph read the same in both themes; the footer uses the
 * surface tokens and follows the theme.
 *
 * Text and the play glyph are sized in `em`, so a card inherits its scale from
 * wherever it sits: the sample grid leaves it at the body size and the hero
 * stack sets a step larger (CF-681). The micro text is 0.64em but floored at
 * 12px (DESIGN.md: nothing below 12px), so in the sample grid it is 12px and
 * in the hero 12.8px; the confidence is 0.8em.
 */
export interface MockClip {
  action: string;
  confidence: number;
  time: string;
  duration: string;
  /** Gradient start for the thumbnail, e.g. `from-red-950`. */
  thumbFrom: string;
  dotClass: string;
  confClass: string;
}

export function MockClipCard({
  action, confidence, time, duration, thumbFrom, dotClass, confClass,
  className = "w-[210px]",
}: MockClip & { className?: string }) {
  return (
    <div
      className={`overflow-hidden rounded-xl border border-border bg-surface shadow-xl shadow-black/20 select-none ${className}`}
    >
      {/* Thumbnail */}
      <div className={`relative aspect-video bg-gradient-to-br ${thumbFrom} to-zinc-900 flex items-center justify-center`}>
        {/* Action badge */}
        <div className="absolute top-2 left-2 flex items-center gap-1.5 rounded bg-black/50 backdrop-blur-sm px-2 py-0.5">
          <span className={`h-1 w-1 rounded-full shrink-0 ${dotClass}`} />
          <span className="text-[max(0.75rem,0.64em)] font-semibold uppercase tracking-widest text-white/70">
            {action}
          </span>
        </div>
        {/* Play button */}
        <div className="flex h-[2.25em] w-[2.25em] items-center justify-center rounded-full bg-white/8 border border-white/12">
          <svg className="ml-0.5 h-[0.875em] w-[0.875em] fill-white/50" viewBox="0 0 24 24" aria-hidden>
            <path d="M8 5v14l11-7z" />
          </svg>
        </div>
        {/* Duration */}
        <div className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[max(0.75rem,0.64em)] font-medium text-white/75 tabular-nums">
          {duration}
        </div>
      </div>
      {/* Footer */}
      <div className="flex items-center justify-between px-2.5 py-2 border-t border-border">
        <span className={`text-[0.8em] font-semibold tabular-nums ${confClass}`}>
          {confidence}%
        </span>
        <span className="text-[max(0.75rem,0.64em)] text-subtle tabular-nums">{time}</span>
      </div>
    </div>
  );
}
