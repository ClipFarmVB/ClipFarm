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
 *
 * The bar is not pure throughput, though (CF-255). Stage boundaries and
 * condensing's runtime re-budget (`rebudget_final_stage`) jump it forward,
 * and some stages report nothing until they end. So the window *rebases*
 * past a jump (shifts the earlier samples up by it, so it adds no velocity)
 * instead of clearing, and a flat window *carries* the last estimate,
 * counted down by wall-clock time, instead of dropping to null.
 *
 * `stepEta` is the whole estimator: a pure reducer the page calls once per
 * poll with the state it returned last time.
 */

export interface ProgressSample {
  tMs: number;
  progress: number;
}

export interface EtaState {
  /** Velocity window, oldest first. Earlier samples are rebased past jumps,
   * so only the newest one is guaranteed to hold the reported progress. */
  samples: ProgressSample[];
  /** Smoothed estimate as of `etaAtMs`; it decays by wall-clock time after. */
  etaSeconds: number | null;
  etaAtMs: number | null;
  /** When progress last moved forward; the flat ceiling counts from here. */
  advancedAtMs: number | null;
  stage: string | null;
}

export interface EtaPoll {
  tMs: number;
  progress: number;
  stage: string | null;
}

export interface EtaStep {
  state: EtaState;
  /** Seconds to show; null means show the stage label, not a number. */
  etaSeconds: number | null;
}

export const INITIAL_ETA_STATE: EtaState = {
  samples: [],
  etaSeconds: null,
  etaAtMs: null,
  advancedAtMs: null,
  stage: null,
};

// Need at least this much observation before showing a number.
const MIN_WINDOW_SECONDS = 15;
const MIN_PROGRESS = 0.03;

// Velocity window: samples older than this are dropped (stale stages).
const WINDOW_MS = 90_000;

// A within-stage step is a re-tiling jump, not throughput, when its rate
// (step / seconds since the previous poll, so a throttled background tab's
// long gaps don't read as bursts) is this many times the window's rate...
const JUMP_RATE_MULTIPLE = 10;
// ...and it is at least this big. The worker writes in >=1pp steps, so on a
// slow stage one ordinary write can be 10x the window's average rate; three
// write quanta is past what that quantization alone produces.
const JUMP_MIN_STEP = 0.03;

// A flat window (an opaque stage, or the bar frozen at its high-water mark
// while Modal fails over to local CPU) shows the held estimate counting down.
// Once it has been flat for this fraction of that estimate it is more likely
// stalled than opaque, and the countdown would end on a false "less than a
// minute": drop to null so the UI shows the stage label instead.
const FLAT_CEILING_FRACTION = 0.5;
// Never drop sooner than this: progress moves in >=1pp writes, so near the end
// of a run a short held estimate would otherwise flicker out between writes.
const STALE_SECONDS = 45;

// Weight of the newest raw estimate in the smoothed value.
const EMA_ALPHA = 0.3;

/** Seconds of the held estimate still left at `tMs`, or null if none. */
function decayedEta(state: EtaState, tMs: number): number | null {
  if (state.etaSeconds === null || state.etaAtMs === null) return null;
  return Math.max(0, state.etaSeconds - (tMs - state.etaAtMs) / 1000);
}

/** Progress per second across the window, or null while it is too short to
 * mean anything or flat. */
function observedRate(samples: ProgressSample[]): number | null {
  if (samples.length < 2) return null;
  const first = samples[0];
  const last = samples[samples.length - 1];
  const seconds = (last.tMs - first.tMs) / 1000;
  if (seconds < MIN_WINDOW_SECONDS) return null;
  const rate = (last.progress - first.progress) / seconds;
  return rate > 0 ? rate : null;
}

/** Fold one poll into the estimator. */
export function stepEta(state: EtaState, poll: EtaPoll): EtaStep {
  const { tMs, progress, stage } = poll;

  // A backwards step is a retry (`_sync_db` zeroes progress on re-entering
  // "processing"): nothing before it describes this run. Start over.
  const prev = state.samples[state.samples.length - 1];
  if (prev && progress < prev.progress) state = INITIAL_ETA_STATE;
  const last = state.samples[state.samples.length - 1];

  let samples = state.samples.filter((s) => tMs - s.tMs <= WINDOW_MS);
  let advancedAtMs = state.advancedAtMs ?? tMs;
  if (last && progress > last.progress) {
    advancedAtMs = tMs;
    const step = progress - last.progress;
    const dtSeconds = Math.max((tMs - last.tMs) / 1000, 1e-3);
    const rate = observedRate(samples);
    // A stage change is always a re-tiling point (its forced boundary write,
    // and condensing's re-budget, which usually lands on the same poll). A
    // within-stage step is one only when it is far above the observed rate;
    // with no observed rate yet, it counts as throughput.
    const isJump = stage !== state.stage
      || (rate !== null && step >= JUMP_MIN_STEP && step / dtSeconds > JUMP_RATE_MULTIPLE * rate);
    if (isJump) {
      // Credit the step with what the observed rate would have produced over
      // the gap, and shift the earlier samples up by the rest.
      const excess = Math.max(0, step - (rate ?? 0) * dtSeconds);
      samples = samples.map((s) => ({ tMs: s.tMs, progress: s.progress + excess }));
    }
  }
  samples.push({ tMs, progress });

  const next: EtaState = { ...state, samples, advancedAtMs, stage };
  const dropped: EtaStep = { state: { ...next, etaSeconds: null, etaAtMs: null }, etaSeconds: null };
  const held = decayedEta(state, tMs);
  // Carrying keeps the state's anchor (etaSeconds, etaAtMs) as it was, so the
  // countdown is always measured from the last real estimate.
  const carried: EtaStep = { state: next, etaSeconds: held !== null && held > 0 ? held : null };

  if (progress < MIN_PROGRESS || progress >= 1) return dropped;

  const first = samples[0];
  if (progress <= first.progress) {
    // Flat window: an opaque stage, or a stall. Count the held estimate down
    // until it runs out or the flat stretch outlasts the ceiling.
    const flatSeconds = (tMs - advancedAtMs) / 1000;
    const ceiling = Math.max(STALE_SECONDS, FLAT_CEILING_FRACTION * (state.etaSeconds ?? 0));
    return flatSeconds >= ceiling ? dropped : carried;
  }

  // Velocity up to the last advance, not up to now: polls between the
  // worker's writes, and the start of an opaque stage, are not a slowdown.
  // Counting them in would shrink the velocity poll by poll and inflate the
  // estimate until the window went flat. Instead the time since the last
  // advance counts down from the estimate as of then.
  const movingSeconds = (advancedAtMs - first.tMs) / 1000;
  if (movingSeconds < MIN_WINDOW_SECONDS) return carried;
  const velocity = (progress - first.progress) / movingSeconds; // fraction/sec
  const raw = Math.max(0, (1 - progress) / velocity - (tMs - advancedAtMs) / 1000);
  const eta = held === null ? raw : EMA_ALPHA * raw + (1 - EMA_ALPHA) * held;
  return { state: { ...next, etaSeconds: eta, etaAtMs: tMs }, etaSeconds: eta };
}

export function formatEta(seconds: number): string {
  if (seconds < 60) return "less than a minute left";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `about ${minutes} min left`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `about ${hours}h ${rest}m left` : `about ${hours}h left`;
}
