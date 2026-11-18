// The site background for the public pages (CF-679): an evocation of the
// standard indoor match ball — big swooping red, white and green panels with a
// fine honeycomb texture embossed over them.
//
// Design choices, and why:
//
// - Two corner groups, not a full wrap. Taken literally the panels would run
//   behind the headline and the clip grid, and full-strength red/green under a
//   video is loud. Instead the panels enter from the top-right and bottom-left
//   corners of the page, as if it were a zoomed-in patch of the ball, and the
//   middle stays the plain page colour where the content sits.
// - The panels are drawn the way the real ones are cut, which is what makes
//   this read as the ball rather than as rainbows. Each group is one sweep
//   around its corner: a flat white block that ends on a straight seam, a fat
//   red crescent that starts blunt at that seam and tapers to a blade point,
//   a thin white band nested along the crescent's inner edge that tapers to
//   its own point, and a green wedge filling the corner. The crescent's two
//   edges bow in opposite directions (outer convex, inner concave) so its
//   width keeps changing, and the bands converge at the page edge the way the
//   panels converge at the ball's silhouette. The bottom-left group is the
//   same drawing turned through 180° with the colours swapped, so the two
//   corners alternate colour / white / colour like the real panels.
// - Tinted, not painted. The panel colours are the token tints in globals.css
//   (`--cf-ball-*`), around 15% alpha in the light theme and 18% in the dark,
//   so cream reads as a blush and a mint and near-black reads as wine and
//   forest. Body text keeps its contrast on top of them.
// - The honeycomb is a tiled 16x27 SVG, not an image; see `.molten-hex`.
//
// The paths are hand-drawn Béziers, not traced from any ball. No marks or
// text: the ball's artwork is Molten's trade dress and this only evokes it.
//
// The layer scrolls with the page. It is `absolute inset-0` inside the root
// layout's `<main>`, which is `relative` and `min-h-screen`, so the layer is
// exactly as tall as the document: the honeycomb runs the whole length, the
// top-right group sits at the top of the page and the bottom-left group at
// its foot. A one-screen page (login, signup) therefore looks the same as it
// did when the layer was fixed; on the landing the groups bracket the whole
// page and the texture carries the middle. From `lg` the sidebar is a
// permanent 220px column and `<main>` starts past it, so the layer does too,
// rather than drawing a swoop that the sidebar would then cover. Nothing in
// the layer animates. Mount it as a direct child of the page, outside any
// `.fade-up` root, so the only positioned ancestor is `<main>`.

export function MoltenBackdrop() {
  return (
    <div
      aria-hidden
      data-testid="molten-backdrop"
      className="pointer-events-none absolute inset-0 -z-10 overflow-hidden"
    >
      {/* Top-right group, from the outside in: a white block on a straight
          seam, the red crescent, the white band, the green corner wedge. */}
      <svg
        className="absolute top-0 right-0 w-[clamp(320px,70vw,980px)]"
        viewBox="0 0 1000 1000"
        fill="none"
      >
        <path className="molten-white" d={PANEL_BLOCK} />
        <path className="molten-red" d={PANEL_CRESCENT} />
        <path className="molten-white" d={PANEL_BAND} />
        <path className="molten-green" d={PANEL_WEDGE} />
      </svg>

      {/* Bottom-left group: the same sweep turned through 180°, with the
          green crescent outside and the red wedge in the corner. */}
      <svg
        className="absolute bottom-0 left-0 w-[clamp(320px,70vw,980px)]"
        viewBox="0 0 1000 1000"
        fill="none"
      >
        <g transform="rotate(180 500 500)">
          <path className="molten-white" d={PANEL_BLOCK} />
          <path className="molten-green" d={PANEL_CRESCENT} />
          <path className="molten-white" d={PANEL_BAND} />
          <path className="molten-red" d={PANEL_WEDGE} />
        </g>
      </svg>

      <div className="molten-hex absolute inset-0" />
    </div>
  );
}

// One sweep around the top-right corner of a 1000x1000 field. Neighbouring
// panels share their seam curve exactly, so no page colour shows between them.

/** The flat block: top edge, a gently curved left side, a straight seam. */
const PANEL_BLOCK = "M330 0 C340 60 365 112 400 150 L640 0 Z";

/** The crescent: blunt on the seam, convex outer edge, concave inner edge,
 *  a blade point short of the right edge. */
const PANEL_CRESCENT = "M400 150 C430 360 640 560 950 620 C880 330 720 100 640 0 Z";

/** The band: nested along the crescent's inner edge, past its tip, tapering
 *  to a point on the right edge. */
const PANEL_BAND =
  "M640 0 C720 100 880 330 950 620 C970 645 990 668 1000 690 C990 350 850 100 760 0 Z";

/** The corner wedge, bounded by the band's inner edge. */
const PANEL_WEDGE = "M760 0 C850 100 990 350 1000 690 L1000 0 Z";
