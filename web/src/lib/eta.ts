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
 * and some stages report nothing until they end. So a jump *restarts* the
 * velocity window at the jump poll, and the last estimate is *carried*,
 * counted down by wall-clock time, until the fresh window can give a
 * velocity, instead of dropping to null.
 *
 * Velocity is measured between the worker's writes (the polls where
 * progress advanced), not up to now: the polls between writes, and an
 * opaque stage, are not a slowdown, and a slow stage that writes in 1pp
 * steps needs its previous write, not an arbitrary poll, as the anchor.
 * A flat stretch the polls watched for much longer than the window's own
 * gap between writes is a freeze, not a slow write, so the advance that
 * ends it restarts the window too.
 *
 * `stepEta` is the whole estimator: a pure reducer the page calls once per
 * poll with the state it returned last time.
 */

export interface ProgressSample {
  tMs: number;
  progress: number;
}

export interface EtaState {
  /** The polls where progress advanced since the window last restarted,
   * oldest first, as reported. The newest is the current progress. */
  samples: readonly ProgressSample[];
  /** Smoothed estimate as of `etaAtMs`; it decays by wall-clock time after. */
  etaSeconds: number | null;
  etaAtMs: number | null;
  /** When progress last moved forward; the flat ceiling counts from here. */
  advancedAtMs: number | null;
  stage: string | null;
  /** False from a stage change until the stage's first advance on a later
   * poll, which is a re-tiling point too (condensing's re-budget is written
   * after `stage("condensing")`, often a poll later). */
  settled: boolean;
  /** The flat ceiling dropped a held estimate: the flat stretch was a stall,
   * so the advance that ends it starts a fresh window. */
  stalled: boolean;
  /** The previous poll. Polls since the last write that still saw it flat
   * are what tell a freeze from a long gap between polls. */
  polledAtMs: number | null;
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

export const INITIAL_ETA_STATE: EtaState = Object.freeze({
  samples: Object.freeze([]),
  etaSeconds: null,
  etaAtMs: null,
  advancedAtMs: null,
  stage: null,
  settled: false,
  stalled: false,
  polledAtMs: null,
});

// Need at least this much observation before showing a number.
const MIN_WINDOW_SECONDS = 15;
const MIN_PROGRESS = 0.03;

// Velocity window: writes older than this are dropped, except the newest of
// them, kept as the anchor so a stage writing less often than this still
// has an interval to measure.
const WINDOW_MS = 90_000;

// A within-stage step is a re-tiling jump, not throughput, when its rate
// (step / seconds since the previous write, so neither a throttled
// background tab's long poll gaps nor a slow stage's 1pp staircase read as
// bursts) is this many times the window's rate.
const JUMP_RATE_MULTIPLE = 10;

// A flat stretch (an opaque stage, or the bar frozen at its high-water mark
// while Modal fails over to local CPU) shows the held estimate counting down.
// Once it has been flat for this fraction of that estimate it is more likely
// stalled than opaque, and the countdown would end on a false "less than a
// minute": drop to null so the UI shows the stage label instead.
const FLAT_CEILING_FRACTION = 0.5;
// Never drop sooner than this: progress moves in >=1pp writes, so near the end
// of a run a short held estimate would otherwise flicker out between writes.
const STALE_SECONDS = 45;

// A flat stretch the polls saw for more than this many times the window's
// mean gap between writes is a freeze (the bar held at its high-water mark
// while Modal fails over to local CPU), not the gap before the next write.
// A 1pp staircase is flat for about one gap, so it stays throughput.
const FREEZE_GAP_MULTIPLE = 3;

// A fresh window's first estimate replaces the carried one outright when the
// two differ by more than this factor: the new stage (or the resumed one) runs
// at a different speed, and the EMA would take a minute to agree.
const RESEED_FACTOR = 3;

// Weight of the newest raw estimate in the smoothed value.
const EMA_ALPHA = 0.3;

/** Seconds of the held estimate still left at `tMs`, or null if none. */
function decayedEta(state: EtaState, tMs: number): number | null {
  if (state.etaSeconds === null || state.etaAtMs === null) return null;
  return Math.max(0, state.etaSeconds - (tMs - state.etaAtMs) / 1000);
}

/** Progress per second between the window's first and last write, or null
 * while they are too close together to mean anything. */
function windowRate(samples: readonly ProgressSample[]): number | null {
  if (samples.length < 2) return null;
  const first = samples[0];
  const last = samples[samples.length - 1];
  const seconds = (last.tMs - first.tMs) / 1000;
  if (seconds < MIN_WINDOW_SECONDS) return null;
  const rate = (last.progress - first.progress) / seconds;
  return rate > 0 ? rate : null;
}

/** Mean seconds between the window's writes, or null with only one. */
function meanWriteGapSeconds(samples: readonly ProgressSample[]): number | null {
  if (samples.length < 2) return null;
  return (samples[samples.length - 1].tMs - samples[0].tMs) / 1000 / (samples.length - 1);
}

/** Drop writes older than the window, keeping the newest of them. */
function trimWindow(samples: readonly ProgressSample[], tMs: number): ProgressSample[] {
  let start = 0;
  while (start + 1 < samples.length && tMs - samples[start + 1].tMs > WINDOW_MS) start++;
  return samples.slice(start);
}

/** Fold one poll into the estimator. */
export function stepEta(state: EtaState, poll: EtaPoll): EtaStep {
  const { tMs, progress, stage } = poll;

  // A backwards step is a retry (`_sync_db` zeroes progress on re-entering
  // "processing"): nothing before it describes this run. Start over.
  const prev = state.samples[state.samples.length - 1];
  if (prev && progress < prev.progress) state = INITIAL_ETA_STATE;
  const last = state.samples[state.samples.length - 1];
  const stageChanged = stage !== state.stage;
  const held = decayedEta(state, tMs);

  if (!last || progress > last.progress) {
    let samples: ProgressSample[];
    let settled = state.settled && !stageChanged;
    if (!last) {
      samples = [{ tMs, progress }];
    } else {
      const step = progress - last.progress;
      const rate = windowRate(state.samples);
      const sinceWriteSeconds = Math.max((tMs - last.tMs) / 1000, 1e-3);
      // A stage change is always a re-tiling point (its forced boundary
      // write), and so is the stage's first advance after it. A step inside a
      // settled stage is one only when it is far above the observed rate; with
      // no observed rate yet, it counts as throughput.
      const isJump = stageChanged
        || !state.settled
        || (rate !== null && step / sinceWriteSeconds > JUMP_RATE_MULTIPLE * rate);
      if (!stageChanged) settled = true;
      // Flat time the polls saw, from the last write to the previous poll. A
      // throttled tab's long poll gap saw none of it, so it stays throughput.
      const watchedFlatSeconds = ((state.polledAtMs ?? last.tMs) - last.tMs) / 1000;
      const gap = meanWriteGapSeconds(state.samples);
      const isFreeze = gap !== null && watchedFlatSeconds > FREEZE_GAP_MULTIPLE * gap;
      // A jump, or the end of a freeze or stall, restarts the window here:
      // neither the step nor the flat time before it is velocity. The held
      // estimate carries until the fresh window is long enough to measure.
      samples = isJump || isFreeze || state.stalled
        ? [{ tMs, progress }]
        : [...trimWindow(state.samples, tMs), { tMs, progress }];
    }
    const next: EtaState = {
      ...state, samples, advancedAtMs: tMs, stage, settled, stalled: false, polledAtMs: tMs,
    };
    if (progress < MIN_PROGRESS || progress >= 1) {
      return { state: { ...next, etaSeconds: null, etaAtMs: null }, etaSeconds: null };
    }
    const velocity = windowRate(samples); // fraction/sec
    if (velocity === null) {
      // Carrying keeps the anchor (etaSeconds, etaAtMs) as it was, so the
      // countdown is always measured from the last real estimate.
      return { state: next, etaSeconds: held !== null && held > 0 ? held : null };
    }
    const raw = (1 - progress) / velocity;
    // A held estimate that has fully decayed says nothing; blending against
    // its zero would cut the new estimate to a third. Nor does one carried
    // into a fresh window that measures a very different speed.
    const fresh = windowRate(state.samples) === null;
    const disagrees = held !== null && (raw > RESEED_FACTOR * held || held > RESEED_FACTOR * raw);
    const eta = held === null || held <= 0 || (fresh && disagrees)
      ? raw
      : EMA_ALPHA * raw + (1 - EMA_ALPHA) * held;
    return { state: { ...next, etaSeconds: eta, etaAtMs: tMs }, etaSeconds: eta };
  }

  // No advance: between writes, an opaque stage, or a stall. Count the held
  // estimate down until it runs out or the flat stretch outlasts the ceiling.
  // A stage change with no step marks the next advance as a re-tiling point.
  const next: EtaState = { ...state, stage, settled: state.settled && !stageChanged, polledAtMs: tMs };
  if (progress < MIN_PROGRESS || progress >= 1) {
    return { state: { ...next, etaSeconds: null, etaAtMs: null }, etaSeconds: null };
  }
  const flatSeconds = (tMs - (state.advancedAtMs ?? tMs)) / 1000;
  const ceiling = Math.max(STALE_SECONDS, FLAT_CEILING_FRACTION * (state.etaSeconds ?? 0));
  if (flatSeconds >= ceiling) {
    // Only giving up on a real estimate marks a stall: before the first
    // estimate, a slow stage's gap between writes is just a gap.
    const stalled = state.stalled || state.etaSeconds !== null;
    return { state: { ...next, etaSeconds: null, etaAtMs: null, stalled }, etaSeconds: null };
  }
  return { state: next, etaSeconds: held !== null && held > 0 ? held : null };
}

export function formatEta(seconds: number): string {
  if (seconds < 60) return "less than a minute left";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `about ${minutes} min left`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `about ${hours}h ${rest}m left` : `about ${hours}h left`;
}
