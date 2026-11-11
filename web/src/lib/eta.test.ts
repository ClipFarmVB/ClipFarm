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
  reported = new Map<number, number>();

  poll(p: Partial<EtaPoll> = {}): number | null {
    this.tMs = p.tMs ?? this.tMs;
    this.progress = p.progress ?? this.progress;
    this.stage = p.stage === undefined ? this.stage : p.stage;
    this.reported.set(this.tMs, this.progress);
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

  /** How far each window sample sits above what was reported at its time. */
  shifts(): number[] {
    return this.state.samples.map((s) => s.progress - this.reported.get(s.tMs)!);
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

  it("rebases a stage change's step even when it is small", () => {
    const run = warmedUp(0.5, 0.001, "cutting_clips");
    // Boundary write plus a re-budget, landing on one poll: 2pp, under the
    // within-stage jump floor.
    run.poll({ tMs: run.tMs + POLL_MS, progress: run.progress + 0.02, stage: "condensing" });
    // Everything but the 5s the observed rate accounts for is rebased.
    const shifts = run.shifts();
    for (const shift of shifts.slice(0, -1)) expect(shift).toBeCloseTo(0.02 - 0.001 * 5, 6);
    expect(shifts[shifts.length - 1]).toBe(0);
  });

  it("does not hide a stage boundary behind null", () => {
    const run = warmedUp(0.1, 0.001);
    const before = run.last()!;
    const boundary = run.poll({ tMs: run.tMs + POLL_MS, progress: run.progress + 0.005, stage: "scoring_highlights" });
    expect(boundary).not.toBeNull();
    expect(boundary!).toBeCloseTo(before - POLL_MS / 1000, -1);
  });

  it("follows a real speed-up instead of rebasing it", () => {
    const run = warmedUp(0.05, 0.001);
    // 7x faster, 3.5pp a poll: over the jump floor, under the jump multiple.
    run.steady(60, 0.007);
    expect(run.shifts().every((shift) => shift === 0)).toBe(true);
    // Well under what the old rate would say.
    expect(run.last()!).toBeLessThan(0.5 * (1 - run.progress) / 0.001);
  });

  it("judges a step by its rate, so a throttled background tab is not a jump", () => {
    const run = warmedUp(0.1, 0.001);
    // A 60s gap between polls, the stage running a little faster meanwhile:
    // 9pp in one step, but only 1.5x the observed rate.
    run.poll({ tMs: run.tMs + 60_000, progress: run.progress + 0.09 });
    expect(run.shifts().every((shift) => shift === 0)).toBe(true);
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
    };
    const { etaSeconds } = stepEta(state, { tMs: 60_000, progress: 0.56, stage: "condensing" });
    const raw = (1 - 0.56) / (0.06 / 60);
    expect(etaSeconds).toBeCloseTo(0.3 * raw + 0.7 * (600 - 60), 6);
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
