/**
 * The ClipFarm mark and wordmark, as they appear in the sidebar's two
 * placements — the mobile top bar and the desktop rail (CF-260).
 *
 * The mark is the logo's line-art player (CF-573), drawn as a CSS mask over
 * `bg-brand` rather than as an <img>, so it takes the theme's accent: the
 * logo teal on cream in the light theme, the brighter teal in the dark one.
 * `public/brand/mark-mask.png` is the logo's figure cut to an alpha channel,
 * with its strokes thickened slightly so they survive at 26px.
 *
 * Only the mark and wordmark live here. The wrapping <Link> stays at each call
 * site because the two differ for real reasons: the rail's version closes the
 * drawer on navigate and shares a flex row with a close button.
 *
 * That means both of this component's couplings to its parent live on that
 * <Link>, because it returns a bare fragment: `flex items-center gap-2.5` is
 * what lays the mark and wordmark out beside each other, and `group` is what
 * drives the `group-hover:` on the mark. Both call sites carry all of it. The
 * layout half breaks visibly the moment it is missing; the hover half just
 * stops working quietly, which is why both are written down here rather than
 * left to be rediscovered by breaking them.
 */
import { LogoMark } from "@/components/LogoMark";

export { MARK_MASK_SRC } from "@/components/LogoMark";

export function BrandMark() {
  return (
    <>
      <LogoMark className="h-[26px] w-[26px] transition-opacity group-hover:opacity-80" />
      <span className="text-[14px] font-semibold tracking-tight text-foreground">
        ClipFarm
      </span>
    </>
  );
}
