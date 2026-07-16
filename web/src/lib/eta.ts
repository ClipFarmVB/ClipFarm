/**
 * Time-remaining estimation for game processing.
 *
 * Pure extrapolation from overall progress: the worker's stage spans are
 * weighted to approximate wall-clock shares, so
 *   eta = elapsed * (1 - p) / p
 * self-corrects as the run advances. EMA smoothing keeps the number from
 * yo-yoing between polls; coarse formatting absorbs the remaining error
 * (and any client/server clock skew).
 */

// Below these, the extrapolation is dominated by noise — show nothing.
const MIN_PROGRESS = 0.05;
const MIN_ELAPSED_SECONDS = 20;

// Weight of the newest raw estimate in the smoothed value.
const EMA_ALPHA = 0.3;

export function estimateEtaSeconds(
  progress: number,
  startedAtIso: string | null,
  nowMs: number,
  prevEtaSeconds: number | null,
  prevProgress: number | null = null,
): number | null {
  if (!startedAtIso || progress < MIN_PROGRESS || progress >= 1) return null;
  const startedMs = Date.parse(startedAtIso);
  if (Number.isNaN(startedMs)) return null;

  const elapsed = (nowMs - startedMs) / 1000;
  if (elapsed < MIN_ELAPSED_SECONDS) return null;

  // Flat progress (an opaque remote stage, a stall): hold the previous
  // estimate rather than letting elapsed-time extrapolation inflate it —
  // a user should never watch "6 min left" climb to "11 min left".
  if (prevEtaSeconds !== null && prevProgress !== null && progress <= prevProgress) {
    return prevEtaSeconds;
  }

  const raw = (elapsed * (1 - progress)) / progress;
  if (prevEtaSeconds === null) return raw;
  return EMA_ALPHA * raw + (1 - EMA_ALPHA) * prevEtaSeconds;
}

export function formatEta(seconds: number): string {
  if (seconds < 60) return "less than a minute left";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `about ${minutes} min left`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `about ${hours}h ${rest}m left` : `about ${hours}h left`;
}
