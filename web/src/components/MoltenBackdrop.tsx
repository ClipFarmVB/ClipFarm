"use client";

import { useEffect, useRef, useState } from "react";

// The site background for the public pages (CF-679): an evocation of the
// standard indoor match ball — the big interlocking red, white and green
// panels with a fine honeycomb texture embossed over them.
//
// How the face is built, and why:
//
// - One composition, the way the ball's face is cut, not corner ornaments.
//   The face is three interlocking hooks. A white S sweeps diagonally from
//   the upper right through the middle to the lower left: fattest in the
//   centre, about a third of the width, tapering to a blade point at both
//   ends, its two edges offset S-curves so it is a swoosh rather than an even
//   band. A red hook wraps the top and the left: its outer edge is the
//   straight seam under the top white block, it turns a rounded elbow at the
//   upper left where it is thickest, and its inner edge is the S's own edge.
//   A green hook does the same along the bottom and the right. Flat white
//   seam blocks close the top and bottom, each with a small triangle of the
//   next group's colour cutting in, and at the far left and right a thin
//   crescent of the neighbouring group shows round the edge. The whole face
//   has 180° rotational symmetry with red and green swapped, which is the
//   thing that makes it read as this ball and not as abstract shapes, so the
//   green half is literally the red half turned round.
// - One face per screen, stacked down the page and alternated. The layer is
//   as tall as the document (see below) and holds a column of faces each
//   `100vh` tall. A one-screen page (login, signup) gets exactly one face.
//   The landing gets two and a bit, each face turned 180° from the one above
//   it, which by the symmetry is the same drawing with the colours swapped:
//   the red hook sits top-left on the first screen, the green hook top-left
//   on the second. Where two faces meet their seam blocks make one white
//   band with the two triangles in it, like the seam the real panels share.
//   Alternating rather than stretching one face keeps the S at a readable
//   scale on a phone, where the page is several screens tall, and fills its
//   middle instead of leaving bare texture.
// - The face is drawn in a square and stretched to the screen's aspect
//   (`preserveAspectRatio="none"`): wider than tall on a desktop, much
//   taller than wide on a phone. The S stays an S either way, and the
//   curves are the gentler ones from the centre of the ball rather than the
//   hard bends near its rim, which is what survives stretching.
// - Tinted, not painted. The colours are the token tints in globals.css
//   (`--cf-ball-*`), around 15% alpha in the light theme and 18% in the dark,
//   so cream reads as a blush and a mint and near-black reads as wine and
//   forest, and the content keeps its contrast on top of them.
// - The honeycomb is a tiled 16x27 SVG, not an image; see `.molten-hex`.
//
// The paths are hand-drawn Béziers, not traced from any ball. No marks or
// text: the ball's artwork is Molten's trade dress and this only evokes it.
//
// The layer scrolls with the page. It is `absolute inset-0` inside the root
// layout's `<main>`, which is `relative` and `min-h-screen`, so the layer is
// exactly as tall as the document and `overflow-hidden` crops the column of
// faces to it. The number of faces comes from the page, not a constant: the
// layer measures its own height against a face's and renders enough to cover
// it, again whenever either changes (content loading, a resize, rotating a
// phone). A fixed count ran out on short screens, where the landing is many
// screens tall (812x375 needs six), and the panels stopped on a hard edge.
// Before the first measurement it renders `INITIAL_FACES`. From `lg` the
// sidebar is a permanent 220px column and `<main>` starts past it, so the
// layer does too. Nothing in the layer animates. Mount
// it as a direct child of the page, outside any `.fade-up` root, so the only
// positioned ancestor is `<main>`.

/** Faces drawn before the layer has been measured (and on the server). */
export const INITIAL_FACES = 4;

/** How many screen-tall faces cover a layer this tall; the layer crops the rest. */
export function facesToCover(layerHeight: number, faceHeight: number): number {
  if (!(layerHeight > 0) || !(faceHeight > 0)) return INITIAL_FACES;
  return Math.max(1, Math.ceil(layerHeight / faceHeight));
}

export function MoltenBackdrop() {
  const layerRef = useRef<HTMLDivElement>(null);
  const [faces, setFaces] = useState(INITIAL_FACES);

  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    const measure = () => {
      const face = layer.querySelector("svg");
      setFaces(
        facesToCover(
          layer.getBoundingClientRect().height,
          face?.getBoundingClientRect().height ?? 0,
        ),
      );
    };
    measure();
    window.addEventListener("resize", measure);
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(layer);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, []);

  return (
    <div
      ref={layerRef}
      aria-hidden
      data-testid="molten-backdrop"
      className="pointer-events-none absolute inset-0 -z-10 overflow-hidden"
    >
      {Array.from({ length: faces }, (_, i) => (
        <Face key={i} turned={i % 2 === 1} />
      ))}
      <div className="molten-hex absolute inset-0" />
    </div>
  );
}

/**
 * One face of the ball, a screen tall. `turned` is the face rotated 180°,
 * which by its symmetry is the same drawing with red and green swapped.
 */
function Face({ turned }: { turned: boolean }) {
  const [top, bottom] = turned
    ? ["molten-green", "molten-red"]
    : ["molten-red", "molten-green"];
  return (
    <svg
      className="block h-screen w-full"
      viewBox="0 0 1000 1000"
      preserveAspectRatio="none"
      fill="none"
    >
      <Half hook={top} crescent={bottom} />
      <g transform="rotate(180 500 500)">
        <Half hook={bottom} crescent={top} />
      </g>
      <path className="molten-white" d={S_BAND} />
    </svg>
  );
}

/** The top-left half: seam block, its triangle, the hook, the left crescent. */
function Half({ hook, crescent }: { hook: string; crescent: string }) {
  return (
    <>
      <path className="molten-white" d={SEAM_BLOCK} />
      <path className={hook} d={SEAM_TRIANGLE} />
      <path className={hook} d={HOOK} />
      <path className="molten-white" d={CRESCENT_RIM} />
      <path className={crescent} d={CRESCENT} />
    </>
  );
}

// Geometry, in a 1000x1000 field. The S's upper edge runs from its upper-right
// tip A (930,130) to its lower-left tip B (70,870) in two Béziers; its lower
// edge is that same curve turned through 180° about the centre, so the band
// is symmetric and the tips are sharp. The hook's inner edge is the upper
// edge verbatim, so the two share their seam exactly.

const S_UPPER_EDGE = "C500 130 430 250 380 380 C330 510 500 870 70 870";
const S_LOWER_EDGE = "C500 870 570 750 620 620 C670 490 500 130 930 130";

/** The white S: upper edge out, lower edge back. */
const S_BAND = `M930 130 ${S_UPPER_EDGE} ${S_LOWER_EDGE} Z`;

/** The hook: along the straight top seam, a wedge at the S's tip, down the
 *  S's edge, and a wedge into the bottom-left corner. */
const HOOK = `M0 70 L960 108 L930 130 ${S_UPPER_EDGE} L0 890 Z`;

/** The flat white strip across the top; its lower edge is the straight seam. */
const SEAM_BLOCK = "M0 0 L1000 0 L1000 110 L0 70 Z";

/** The tip of the next group's hook cutting into the strip at the top right. */
const SEAM_TRIANGLE = "M760 0 L1000 0 L1000 110 Z";

/** The neighbouring group showing round the left edge, with its white rim. */
const CRESCENT_RIM = "M0 180 C130 400 130 650 0 860 Z";
const CRESCENT = "M0 180 C90 400 90 650 0 860 Z";
