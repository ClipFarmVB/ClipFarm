import { actionColors, palette } from "@clipfarm/tokens";
import { describe, expect, it } from "vitest";
import { ACTION_STYLES } from "./Badge";

// The badge palette is the one part of @clipfarm/tokens that web does NOT
// consume: this Badge renders Tailwind class names, mobile's Badge reads
// `actionColors`, and the two are hand-kept duplicates. `themeCss()` emits only
// `ThemeColors`, so the tokens.generated.css snapshot cannot see them — without
// this file the two tables could drift apart silently, in either direction.
//
// So resolve each Tailwind class the same way Tailwind does (its own palette,
// which `palette` holds as hex because React Native cannot parse oklch) and
// compare it to the token entry. Move one side alone and this fails.

/** `red-500` -> `palette.red500`, so the class name picks the hex. */
function hexFor(shade: string): string {
  const [name, step] = shade.split("-");
  const key = `${name}${step}` as keyof typeof palette;
  const hex = palette[key];
  if (!hex) throw new Error(`no palette entry for ${shade} (looked for ${key})`);
  return hex;
}

function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** `bg-red-500/8` -> the `rgba(...)` string `actionColors` spells. */
function bgToRgba(cls: string): string {
  const m = /^bg-([a-z]+-\d+)\/(\d+)$/.exec(cls);
  if (!m) throw new Error(`unparsed bg class: ${cls}`);
  const [r, g, b] = rgb(hexFor(m[1]));
  return `rgba(${r}, ${g}, ${b}, ${(Number(m[2]) / 100).toFixed(2)})`;
}

/** `text-red-400` / `bg-red-400` -> the hex `actionColors` spells. */
function solidToHex(cls: string): string {
  const m = /^(?:text|bg)-([a-z]+-\d+)$/.exec(cls);
  if (!m) throw new Error(`unparsed solid class: ${cls}`);
  return hexFor(m[1]);
}

describe("web's ACTION_STYLES and the token actionColors", () => {
  it("cover exactly the same actions", () => {
    expect(Object.keys(ACTION_STYLES).sort()).toEqual(
      Object.keys(actionColors).sort()
    );
  });

  for (const [action, style] of Object.entries(ACTION_STYLES)) {
    it(`${action} resolves to the same fill, label and dot`, () => {
      const tone = actionColors[action];
      expect(tone, `no actionColors entry for ${action}`).toBeDefined();
      expect(bgToRgba(style.bg), "fill").toBe(tone.bg);
      expect(solidToHex(style.text), "label").toBe(tone.fg);
      expect(solidToHex(style.dot), "dot").toBe(tone.dot);
    });
  }
});
