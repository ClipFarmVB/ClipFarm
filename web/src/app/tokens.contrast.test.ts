import { contrastRatio } from "@clipfarm/tokens";
import { describe, expect, it } from "vitest";

// packages/tokens has no test runner of its own, so its contrast arithmetic is
// pinned here, beside the snapshot that already covers its output. That
// snapshot only exercises opaque foregrounds; these cover the case it cannot:
// translucent text on a translucent fill, which renders as text over (fill
// over backdrop), not text over backdrop.
//
// Expected figures are worked by hand (WCAG 2.x, sRGB):
//   black/60 over white  -> 102,102,102  L = 0.13287
//   white/90 over that   -> 239.7 grey   L = 0.86890  (0.91890 / 0.18287 = 5.02)
//   white/70 over that   -> 209.1 grey   L = 0.63828  (0.68828 / 0.18287 = 3.76)
describe("contrastRatio", () => {
  it("is 21 for opaque white on opaque black", () => {
    expect(contrastRatio("#ffffff", "#000000")).toBeCloseTo(21, 2);
  });

  it("composites the text onto the fill, not the backdrop (white/90 on black/60 over white)", () => {
    expect(
      contrastRatio("rgba(255, 255, 255, 0.9)", "rgba(0, 0, 0, 0.6)", "#ffffff"),
    ).toBeCloseTo(5.02, 2);
  });

  it("keeps the text's own alpha, so white/70 on black/60 over white falls under AA", () => {
    const ratio = contrastRatio(
      "rgba(255, 255, 255, 0.7)",
      "rgba(0, 0, 0, 0.6)",
      "#ffffff",
    );
    expect(ratio).toBeCloseTo(3.76, 2);
    expect(ratio).toBeLessThan(4.5);
  });
});
