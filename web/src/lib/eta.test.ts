import { describe, expect, it } from "vitest";
import { formatEta, INITIAL_ETA_STATE, stepEta, type EtaState, type EtaPoll } from "./eta";

const POLL_MS = 5_000;

/** Feeds polls through the reducer and records what each one would show. */
class Run {
  state: EtaState = INITIAL_ETA_STATE;
  tMs = 0;
  progress = 0;
  stage: string | null = null;
  shown: (number | null)[] = [];

  poll(p: Partial<EtaPoll> = {}): number | null {
    this.tMs = p.tMs ?? this.tMs;
    this.progress = p.progress ?? this.progress;
    this.stage = p.stage === undefined ? this.stage : p.stage;
    const out = stepEta(this.state, { tMs: this.tMs, progress: this.progress, stage: this.stage });
    this.state = out.state;
    this.shown.push(out.etaSeconds);
    return out.etaSeconds;
  }

  /** Polls every 5s for `seconds`, progress moving at `rate` per second. */
  steady(seconds: number, rate: number, stage = this.stage): (number | null)[] {
    const out: (number | null)[] = [];
    for (let t = POLL_MS; t <= seconds * 1000; t += POLL_MS) {
      out.push(this.poll({ tMs: this.tMs + POLL_MS, progress: this.progress + rate * (POLL_MS / 1000), stage }));
    }
    return out;
  }

  last(): number | null {
    return this.shown[this.shown.length - 1];
  }
}

/** A run that has been steady at `rate` from `from` for two minutes. */
function warmedUp(from: number, rate: number, stage = "tracking_ball"): Run {
  const run = new Run();
  run.poll({ tMs: 0, progress: from, stage });
  run.steady(120, rate);
  return run;
}

describe("stepEta", () => {
  it("settles on the remaining fraction over the observed rate", () => {
    const run = warmedUp(0.1, 0.001);
    expect(run.last()).toBeCloseTo((1 - run.progress) / 0.001, 0);
  });

  it("keeps a counting-down number through an opaque stage flat for minutes", () => {
    const run = warmedUp(0.1, 0.001);
    const before = run.last()!;
    run.poll({ tMs: run.tMs + POLL_MS, stage: "refining_actions" });
    const opaque = run.steady(300, 0); // five minutes with a frozen bar
    expect(opaque.every((eta) => eta !== null)).toBe(true);
    // It only counts down, by about the wall-clock time: no climb while the
    // window drains of moving samples, no stop once it has gone flat.
    expect(opaque[0]!).toBeLessThan(before);
    for (let i = 1; i < opaque.length; i++) {
      expect(opaque[i - 1]! - opaque[i]!).toBeCloseTo(POLL_MS / 1000, 0);
    }
  });

  it("does not collapse on the jump out of an opaque stage", () => {
    const run = warmedUp(0.1, 0.001);
    run.poll({ tMs: run.tMs + POLL_MS, stage: "refining_actions" });
    run.steady(120, 0);
    const held = run.last()!;
    // The stage ends: the bar jumps the opaque stage's whole span at once.
    const exit = run.poll({ tMs: run.tMs + POLL_MS, progress: run.progress + 0.12, stage: "cutting_clips" });
    expect(exit).toBeCloseTo(held - POLL_MS / 1000, 0);
  });

  it.each([30, 60, 120])("stays near the true remaining time for every poll after a %ss opaque stage", (flatSeconds) => {
    const run = warmedUp(0.1, 0.001);
    run.poll({ tMs: run.tMs + POLL_MS, stage: "refining_actions" });
    run.steady(flatSeconds, 0);
    run.poll({ tMs: run.tMs + POLL_MS, progress: run.progress + 0.12, stage: "cutting_clips" });
    const after = [run.last(), ...run.steady(90, 0.001)];
    // The next stage runs at 0.001/s, so the truth at each poll is the
    // remaining fraction over that rate. The flat stretch is not velocity.
    after.forEach((eta, i) => {
      const progress = run.progress - 0.001 * 5 * (after.length - 1 - i);
      const remaining = (1 - progress) / 0.001;
      expect(eta).not.toBeNull();
      expect(eta!).toBeLessThan(1.25 * remaining);
      expect(eta!).toBeGreaterThan(0.8 * remaining);
    });
  });

  it("never goes astronomical leaving a long flat stretch at a 4-dp value", () => {
    // A stage flat for 95s at `a` (a stall, or Modal refining before #653's
    // deploy), then the boundary write to `b`. Any arithmetic on the reported
    // values can land a hair off them; the reviewer's sweep found 2.31% of such
    // pairs reading ~1e18 s.
    const pairs: [number, number][] = [[0.05, 0.21]]; // the reviewer's trace
    for (let ai = 0; ai < 60; ai++) {
      const a = Number((0.05 + ai * 0.0097).toFixed(4));
      for (let bi = 1; bi <= 30; bi++) {
        const b = Number((a + bi * 0.0063).toFixed(4));
        if (b < 0.95) pairs.push([a, b]);
      }
    }
    const bad: string[] = [];
    for (const [a, b] of pairs) {
      const run = warmedUp(Number((a - 0.03).toFixed(4)), 0.0002);
      run.poll({ tMs: run.tMs + POLL_MS, progress: a, stage: "refining_actions" });
      run.steady(95, 0);
      run.poll({ tMs: run.tMs + POLL_MS, progress: b, stage: "cutting_clips" });
      // The whole run is under ~80 min at this rate; 1e5 s is the reviewer's
      // line for "astronomical", well clear of any spike or EMA lag.
      for (const eta of [run.last(), ...run.steady(30, 0.0002)]) {
        if (eta !== null && !(eta < 1e5)) bad.push(`${a}->${b}: ${eta}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it.each([
    [100, "1pp per 100s"],
    [50, "1pp per 50s"],
    [33, "1pp per 33s"],
  ])("reads a slow stage that writes in 1pp quanta at its true rate (%ss, %s)", (writeSeconds) => {
    // The worker writes only on a >=1pp change (`_MIN_DELTA`), so a slow stage
    // reports a staircase: flat for `writeSeconds`, then +1pp. Polls every 5s.
    const rate = 0.01 / writeSeconds;
    const run = new Run();
    let written = 0.1;
    const shown: [number, number][] = [];
    for (let t = 0; t <= 1800; t += POLL_MS / 1000) {
      const truth = 0.1 + rate * t;
      if (truth - written >= 0.01 - 1e-9) written = Number(truth.toFixed(4));
      const eta = run.poll({ tMs: t * 1000, progress: written, stage: "tracking_ball" });
      if (t >= 600) shown.push([eta ?? NaN, (1 - truth) / rate]);
    }
    for (const [eta, truth] of shown) {
      expect(eta).toBeGreaterThan(0.85 * truth);
      expect(eta).toBeLessThan(1.15 * truth);
    }
  });

  it("keeps a short held estimate through the gap between writes near the end", () => {
    // About a minute left: half of it is under the gap between 1pp writes, so
    // the ceiling's floor is what keeps the number from flickering out.
    const run = warmedUp(0.85, 0.0008);
    const anchor = run.state.etaSeconds!;
    expect(anchor).toBeLessThan(80);
    expect(run.steady(40, 0).every((eta) => eta !== null)).toBe(true);
  });

  it("does not collapse on a within-stage re-budget jump", () => {
    const run = warmedUp(0.5, 0.001, "condensing");
    run.poll({ tMs: run.tMs + POLL_MS, progress: run.progress + 0.1 }); // rebudget_final_stage
    const after = run.steady(90, 0.001);
    // The true remaining time at the observed rate, poll by poll. Counting the
    // jump as throughput roughly doubles the window's velocity for 90s.
    after.forEach((eta, i) => {
      const remaining = (1 - (run.progress - 0.001 * 5 * (after.length - 1 - i))) / 0.001;
      expect(eta!).toBeGreaterThan(0.85 * remaining);
    });
  });

  it("restarts the window on a stage change's step even when the rate test would pass it", () => {
    const run = warmedUp(0.5, 0.001, "cutting_clips");
    // Boundary write plus a re-budget, landing on one poll: 4pp in 5s is 8x
    // the observed rate, under the within-stage jump multiple.
    run.poll({ tMs: run.tMs + POLL_MS, progress: run.progress + 0.04, stage: "condensing" });
    expect(run.state.samples).toEqual([{ tMs: run.tMs, progress: run.progress }]);
    expect(run.last()).not.toBeNull();
  });

  it("does not count a re-budget written a poll after the stage change as throughput", () => {
    // `stage("condensing")` and `rebudget_final_stage` are separate writes with
    // the keep-window build between them, so they can straddle a poll. The
    // window has just restarted, so there is no rate to judge the step by.
    const run = warmedUp(0.5, 0.001, "cutting_clips");
    run.poll({ tMs: run.tMs + POLL_MS, progress: run.progress + 0.005, stage: "condensing" });
    run.poll({ tMs: run.tMs + POLL_MS, progress: run.progress + 0.02 }); // rebudget_final_stage
    const after = run.steady(90, 0.001);
    after.forEach((eta, i) => {
      const remaining = (1 - (run.progress - 0.001 * 5 * (after.length - 1 - i))) / 0.001;
      expect(eta!).toBeGreaterThan(0.85 * remaining);
    });
  });

  it("does not count a stall as velocity once progress resumes", () => {
    // Modal fails over to local CPU: the bar freezes at its high-water mark
    // past the ceiling, then resumes at the old rate.
    const run = warmedUp(0.1, 0.001);
    const flat = run.steady(run.state.etaSeconds! * 0.6, 0);
    expect(flat[flat.length - 1]).toBeNull();
    const resumed = run.steady(120, 0.001);
    resumed.forEach((eta, i) => {
      if (eta === null) return; // waiting for a fresh window is fine
      const remaining = (1 - (run.progress - 0.001 * 5 * (resumed.length - 1 - i))) / 0.001;
      expect(eta).toBeLessThan(1.2 * remaining);
      expect(eta).toBeGreaterThan(0.8 * remaining);
    });
    expect(resumed[resumed.length - 1]).not.toBeNull();
  });

  it("does not hide a stage boundary behind null", () => {
    const run = warmedUp(0.1, 0.001);
    const before = run.last()!;
    const boundary = run.poll({ tMs: run.tMs + POLL_MS, progress: run.progress + 0.005, stage: "scoring_highlights" });
    expect(boundary).not.toBeNull();
    expect(boundary!).toBeCloseTo(before - POLL_MS / 1000, -1);
  });

  it("follows a real speed-up instead of treating it as a jump", () => {
    const run = warmedUp(0.05, 0.001);
    const before = run.tMs;
    // 7x faster, 3.5pp a poll: under the jump multiple.
    run.steady(60, 0.007);
    // The first faster step stayed in the window as velocity.
    expect(run.state.samples[0].tMs).toBeLessThan(before);
    // Well under what the old rate would say.
    expect(run.last()!).toBeLessThan(0.5 * (1 - run.progress) / 0.001);
  });

  it("judges a step by its rate, so a throttled background tab is not a jump", () => {
    const run = warmedUp(0.1, 0.001);
    // A 60s gap between polls, the stage running a little faster meanwhile:
    // 9pp in one step, but only 1.5x the observed rate.
    run.poll({ tMs: run.tMs + 60_000, progress: run.progress + 0.09 });
    // The window keeps its history: the step is velocity, not a restart.
    expect(run.state.samples.length).toBeGreaterThan(1);
  });

  it("goes null once a flat stretch outlasts the ceiling, before a false countdown", () => {
    const run = warmedUp(0.1, 0.001);
    const anchor = run.state.etaSeconds!;
    run.steady(anchor, 0); // flat for the whole held estimate
    const firstNull = run.shown.findIndex((eta, i) => i > 25 && eta === null);
    expect(firstNull).toBeGreaterThan(0);
    // It stops while the number is still about half the estimate, not at
    // "less than a minute".
    const lastShown = run.shown[firstNull - 1]!;
    expect(lastShown).toBeGreaterThan(0.4 * anchor);
    expect(run.shown.slice(firstNull).every((eta) => eta === null)).toBe(true);
  });

  it("blends the EMA against the held estimate as decayed by wall-clock time", () => {
    const state: EtaState = {
      samples: [{ tMs: 0, progress: 0.5 }],
      etaSeconds: 600,
      etaAtMs: 0,
      advancedAtMs: 0,
      stage: "condensing",
      settled: true,
      stalled: false,
    };
    const { etaSeconds } = stepEta(state, { tMs: 60_000, progress: 0.56, stage: "condensing" });
    const raw = (1 - 0.56) / (0.06 / 60);
    expect(etaSeconds).toBeCloseTo(0.3 * raw + 0.7 * (600 - 60), 6);
  });

  it("does not blend a fresh estimate against a held one that has decayed to zero", () => {
    const state: EtaState = {
      samples: [{ tMs: 0, progress: 0.5 }],
      etaSeconds: 30, // ran out 30s ago
      etaAtMs: 0,
      advancedAtMs: 0,
      stage: "condensing",
      settled: true,
      stalled: false,
    };
    const { etaSeconds } = stepEta(state, { tMs: 60_000, progress: 0.56, stage: "condensing" });
    expect(etaSeconds).toBeCloseTo((1 - 0.56) / (0.06 / 60), 6);
  });

  it("starts over when progress goes backwards (a retry)", () => {
    const run = warmedUp(0.4, 0.001);
    expect(run.last()).not.toBeNull();
    const eta = run.poll({ tMs: run.tMs + POLL_MS, progress: 0.05 });
    expect(eta).toBeNull();
    expect(run.state.samples).toEqual([{ tMs: run.tMs, progress: 0.05 }]);
    expect(run.state.etaSeconds).toBeNull();
  });

  it("shows nothing below the minimum progress, however steady", () => {
    const run = new Run();
    run.poll({ tMs: 0, progress: 0, stage: "downloading" });
    const early = run.steady(25, 0.001); // reaches 0.025, over a 25s window
    expect(early.every((eta) => eta === null)).toBe(true);
    const at = run.steady(10, 0.001); // 0.035
    expect(at[at.length - 1]).not.toBeNull();
  });

  it("shows nothing once progress reaches 1", () => {
    const run = warmedUp(0.9, 0.0008);
    expect(run.poll({ tMs: run.tMs + POLL_MS, progress: 1 })).toBeNull();
  });
});

describe("formatEta", () => {
  it.each([
    [30, "less than a minute left"],
    [59.9, "less than a minute left"],
    [60, "about 1 min left"],
    [14 * 60 + 20, "about 14 min left"],
    [59 * 60 + 40, "about 1h left"],
    [3600, "about 1h left"],
    [3600 + 25 * 60, "about 1h 25m left"],
  ])("formats %s seconds as %s", (seconds, text) => {
    expect(formatEta(seconds)).toBe(text);
  });
});
