import { cn } from "@/lib/utils";

/** The logo's line-art player, cut to an alpha channel (CF-573). */
export const MARK_MASK_SRC = "/brand/mark-mask.png";

/**
 * The ClipFarm logo mark at any size, in the theme's brand colour.
 *
 * Drawn as a CSS mask over `bg-brand` rather than an <img>, so one asset
 * serves both themes: the logo teal on cream in light, the brighter teal in
 * dark. Size it with Tailwind (`h-12 w-12`); it defaults to `bg-brand` and
 * takes any colour class in its place.
 *
 * A <span> rendered as a block, so it is valid inside phrasing content such as
 * a <p> or <label> as well as in a flex row — a <div> there breaks hydration.
 *
 * Decorative by default, since it almost always sits beside the product's
 * name. Pass `label` where it stands alone and needs an accessible name.
 */
export function LogoMark({ className, label }: { className?: string; label?: string }) {
  return (
    <span
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
      className={cn("block shrink-0 bg-brand", className)}
      style={{
        maskImage: `url(${MARK_MASK_SRC})`,
        WebkitMaskImage: `url(${MARK_MASK_SRC})`,
        maskSize: "contain",
        WebkitMaskSize: "contain",
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskPosition: "center",
      }}
    />
  );
}
