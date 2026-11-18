// The site background for the public pages (CF-679): an evocation of the
// standard indoor match ball — big swooping red, white and green panels with a
// fine honeycomb texture embossed over them.
//
// Design choices, and why:
//
// - Two corner groups, not a full wrap. Taken literally the panels would run
//   behind the headline and the clip grid, and full-strength red/green under a
//   video is loud. Instead the panels enter from the top-right and bottom-left
//   corners, as if the page were a zoomed-in patch of the ball, and the middle
//   stays the plain page colour where the content sits. In each corner the
//   bands alternate colour / white / colour the way the real panels do, which
//   is what makes it read as the ball rather than as abstract shapes.
// - Tinted, not painted. The panel colours are the token tints in globals.css
//   (`--cf-ball-*`), around 15% alpha in the light theme and 18% in the dark,
//   so cream reads as a blush and a mint and near-black reads as wine and
//   forest. Body text keeps its contrast on top of them.
// - The honeycomb is a tiled 16x27 SVG, not an image; see `.molten-hex`.
//
// The paths are hand-drawn Béziers, not traced from any ball. No marks or
// text: the ball's artwork is Molten's trade dress and this only evokes it.
//
// The layer is `fixed`, so it is composited once and does not repaint on
// scroll, and nothing in it animates. It must be rendered outside any
// ancestor with a transform (the auth pages' `.fade-up` keeps a `translateY(0)`
// after it finishes), or `fixed` would resolve against that ancestor instead
// of the viewport. From `lg` the sidebar is a permanent 220px column with its
// own opaque background, so the layer starts past it rather than drawing a
// swoop that the sidebar would then cover.

export function MoltenBackdrop() {
  return (
    <div
      aria-hidden
      data-testid="molten-backdrop"
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden lg:left-[220px]"
    >
      {/* Top-right group: red, then white, then a green sliver into the corner. */}
      <svg
        className="absolute -top-[2%] -right-[1%] w-[clamp(320px,70vw,980px)]"
        viewBox="0 0 1000 1000"
        fill="none"
      >
        <path
          className="molten-red"
          d="M430 0 C330 260 300 520 1000 860 L1000 640 C560 480 540 260 620 0 Z"
        />
        <path
          className="molten-white"
          d="M620 0 C540 260 560 480 1000 640 L1000 470 C720 400 690 220 770 0 Z"
        />
        <path
          className="molten-green"
          d="M770 0 C690 220 720 400 1000 470 L1000 320 C860 290 860 160 890 0 Z"
        />
      </svg>

      {/* Bottom-left group: green, then white, then a red sliver into the corner. */}
      <svg
        className="absolute -bottom-[2%] -left-[1%] w-[clamp(320px,70vw,980px)]"
        viewBox="0 0 1000 1000"
        fill="none"
      >
        <path
          className="molten-green"
          d="M570 1000 C670 740 700 480 0 140 L0 360 C440 520 460 740 380 1000 Z"
        />
        <path
          className="molten-white"
          d="M380 1000 C460 740 440 520 0 360 L0 530 C280 600 310 780 230 1000 Z"
        />
        <path
          className="molten-red"
          d="M230 1000 C310 780 280 600 0 530 L0 680 C140 710 140 840 110 1000 Z"
        />
      </svg>

      <div className="molten-hex absolute inset-0" />
    </div>
  );
}
