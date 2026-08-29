/**
 * Time-remaining estimation for game processing.
 *
 * Estimates from *recent progress velocity*: the remaining fraction divided
 * by how fast progress moved over the last ~minute of polls. A global
 * average rate (elapsed * (1-p)/p) systematically climbs during any stage
 * that is slower than its share of the bar — the user watches "5 min left"
 * become "9 min left". Recent velocity is stable within a stage: slow
 * stages show a larger but steady number that shrinks when faster stages
 * arrive. EMA smoothing absorbs poll jitter; coarse formatting absorbs the
 * rest.
 */

export interface ProgressSample {
  tMs: number;
  progress: number;
}

// Need at least this much observation before showing a number.
const MIN_WINDOW_SECONDS = 15;
const MIN_PROGRESS = 0.03;

// Velocity window: samples older than this are dropped (stale stages).
const WINDOW_MS = 90_000;

// If progress hasn't advanced across at least this much of the window, the run
// is genuinely stalled (e.g. Modal died and local-CPU tracking is climbing back
// to the frozen high-water mark) rather than just between polls. Past this we
// drop the held estimate instead of showing a number that's now fiction.
const STALE_SECONDS = 45;

// Weight of the newest raw estimate in the smoothed value.
const EMA_ALPHA = 0.3;

/** Append a sample and drop ones outside the velocity window. */
export function pushSample(
  samples: ProgressSample[], tMs: number, progress: number,
): ProgressSample[] {
  const kept = samples.filter((s) => tMs - s.tMs <= WINDOW_MS);
  kept.push({ tMs, progress });
  return kept;
}

export function estimateEtaSeconds(
  samples: ProgressSample[],
  prevEtaSeconds: number | null,
): number | null {
  if (samples.length < 2) return prevEtaSeconds;
  const first = samples[0];
  const last = samples[samples.length - 1];
  if (last.progress < MIN_PROGRESS || last.progress >= 1) return null;

  const windowSeconds = (last.tMs - first.tMs) / 1000;
  if (windowSeconds < MIN_WINDOW_SECONDS) return prevEtaSeconds;

  const velocity = (last.progress - first.progress) / windowSeconds; // fraction/sec
  if (velocity <= 0) {
    // Flat or opaque stage. Progress moves in ~5s server steps, so hold the
    // previous estimate to ride out the gaps between polls — but once it's been
    // flat across most of the window it's stalled, not paused: drop to null so
    // the UI shows the stage label instead of a frozen "about 4 min left".
    return windowSeconds >= STALE_SECONDS ? null : prevEtaSeconds;
  }

  const raw = (1 - last.progress) / velocity;
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
